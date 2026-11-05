# Session side-channel records (design)

Date: 2026-11-05 · Status: approved-by-delegation (user asleep, autonomous mode)

## Intent and why

dsh writes side-channel records next to the conversation payload in its session
JSONL: a session header (id/createdAt/cwd/version/parent), turn start/end with a
termination reason, retry records, mode/policy records — every line carries
`{type, seq, time, data}`. ZenAgent's session file is pure `Message.to_json()`
lines: it has per-message timestamps and rich assistant metadata (usage, model,
stop_reason, duration), but the *file* is not self-describing and a postmortem
from the file alone is impossible:

- no provenance: nothing says where/when/with which zenagent version a session
  started (only the filename hints at creation time);
- run endings are invisible: the loop computes `termination`
  (stop/max_turns/max_cost/aborted/error/no_stream/tool_terminate) but never
  persists it — an aborted or crashed session just stops mid-file;
- transient LLM retries are swallowed by `stream_with_retry`: a failed attempt
  leaves no trace anywhere;
- mode (plan/ask/zen) changes are never recorded;
- a run without a user prompt (`continue_`/retry-after-resume) is invisible.

## Goals

Model the *necessary* dsh side-channel capabilities on zenagent's own terms:
session provenance, run lifecycle with termination, retry visibility, mode
changes. The transcript stays replayable by every existing consumer.

## Non-goals

- `seq` numbering (line order suffices), per-step start/end records,
  request header/context records (assistant messages already carry
  provider/model), title/todo/job records (sidecars and toolResult messages
  already cover them), any change to `Message.to_json()` itself.

## Approaches considered

1. **Sidecar file** (`<session>.meta.jsonl` next to the JSONL): zero risk to
   the transcript format, but every move/fork/adopt/delete path must learn to
   carry it — the titles/pins sidecars already show how much bookkeeping that
   is, and metadata scattered across files defeats "one file, jq-able".
2. **Records inside the session JSONL (chosen, matches dsh)**: one file stays
   the whole story. Cost: the loader must distinguish side-channel records from
   messages. Decision rule: a top-level JSON object with a string `"type"` and
   no `"role"` key is a side-channel record; `"role"` always wins. Today such
   a line raises "message is missing 'role' key" and is skipped *with a
   warning*; after this change it is collected silently.

## The records

Every side-channel record: `{"type": <str>, "timestamp": <epoch seconds>, ...}`
(UTC epoch seconds, matching message timestamps).

- `session` — written once, lazily, when a new or empty file receives its first
  append (message or meta): `id` (filename stem at creation), `createdAt`,
  `cwd`, `app` ("zenagent"), `appVersion`, and `derivedFrom` (filename stem of
  the source session) when the file was forked/rewritten/compacted. Resumed or
  legacy files (non-empty at first append) never get a header injected.
- `run_start` — at `agent_start`.
- `run_end` — at `agent_end`: `termination`, `detail` (the terminal assistant
  message's `error_message` when the run ended in error), `turns` (LLM requests
  this run; new optional `AgentEvent.turns` field), `duration_ms`.
- `retry` — per transient LLM retry: `attempt`, `max_retries`, `delay_ms`,
  `error`, `model`.
- `mode` — initial mode at bootstrap and on every `set_mode` change: `mode`.

## Mechanism

- Kernel (`zenagent/agent/session.py`): `Session.load()` collects side-channel
  records into `self.records` and the first `session` record into `self.header`
  (no warnings, not messages); `Session.append_meta(record)` appends one record
  (auto-timestamps, swallows OSError — metadata must never kill a run, unlike
  message append); `Session.append` lazily ensures the header;
  `Session.rewrite`/`fork_at` set `derived_from` on the new session; the
  `Session` dataclass gains `derived_from: str = ""`.
- Kernel (`zenagent/retry.py`, `agent/agent.py`, `agent/loop.py`):
  `stream_with_retry(..., on_retry=None)` invokes the callback with
  (message, attempt, max_retries, delay_s) before backing off;
  `AgentConfig.on_retry` / `Agent(on_retry=...)` pass it through.
- App (`zenagent/app/session_meta.py`): `SessionMetaRecorder` — subscribable
  listener writing `run_start`/`run_end` records for the runtime's session;
  runtime bootstrap wires it (after the message-persist subscriber), wires
  `on_retry`, records the initial mode, and records `set_mode` changes.
- App derived sessions: fork (`SessionManager.fork`), compact, and shake set
  `derived_from` on the new session.
- Loading side-channel records changes nothing for other consumers: `usage.py`
  already skips unparseable lines, `list_sessions`/`export`/`compact` read via
  `Session.load()`.

## Left to the builder

Test placement (extend `tests/test_session.py`, `tests/test_retry.py`,
`tests/test_loop.py`, new `tests/test_app_session_meta.py`), exact record key
namespacing, CHANGELOG wording.
