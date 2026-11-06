# Session format → GUI contract, pass 2 (design)

Date: 2026-11-06 · Status: proposed · Scope: C

## Intent and why

The 2026-11-05 pass made the session file self-describing for postmortem:
header, run boundaries, retries, mode. That was enough to reconstruct *what
happened*, not enough to reconstruct *what the UI showed*.

A GUI resumes from `transcript` (view models built from messages) and applies
live events on top. Everything the view layer drops is invisible after a
restart, and three pieces of state were never written at all. The result is a
second-class experience after resume: rows remount, tool cards lose their
diffs and durations, compaction dividers disappear, and steering input becomes
indistinguishable from a normal prompt.

This pass closes that gap. It keeps the 2026-11-05 shape — one append-only
file, flat `{type, timestamp, ...}` records, `Message.to_json()` untouched for
normal messages — and adds only what the contract needs.

## Goals

- **G1** Stable, deterministic view ids in the transcript snapshot.
- **G2** Project already-persisted assistant metadata (timestamp, model,
  provider, usage, duration) into view models.
- **G3** Persist tool completion (`duration_ms`, `is_error`, bounded
  `details`) as `tool_end` side-channel records, and carry them across
  rewrites.
- **G4** Persist a `compaction` record (`tokensBefore`, `derivedFrom`,
  `reason`) and expose it on the transcript snapshot so a resumed session can
  redraw the divider exactly once.
- **G5** Enrich the session-list projection: true `createdAt` from the header,
  last model, `derivedFrom`; keep mtime as "updated".
- **G6** Mark delivered steering input on the user message (`steering: true`),
  additively and only when true, on both the file and the live event.
- **G7** Types, RPC projection, docs and tests for all of the above.

## Non-goals

- Message UUIDs or a revision/variant model. Regenerate keeps zen's
  "rewrite into a new session file" semantics.
- dsh's `{type, seq, time, data}` envelope. Records stay flat.
- Changing the attachment format (base64 stays inline for now).
- Plan history, hosted-search or provider-replay records; zen has no
  corresponding data.
- Subagent child transcripts (`tasks/<stem>/<call_id>.jsonl`). They use a
  different `meta`/`end` vocabulary and never reach `ChatController`; child
  panes stay generic after resume in this pass.
- Per-session usage/cost in the session list. `usage` stays current-transcript
  only; a sidebar showing another session's spend needs a separate request.
- Any change to provider payload construction. The `steering` flag is
  provenance for the file and the UI; adapters never read it.
- GUI implementation. This document fixes the contract; the desktop consumes
  it in the implementation plan.

## Approaches considered

1. **A — projection only** (G1, G2, G5): no format change, lowest risk. Leaves
   tool diffs/durations, compaction dividers and steering provenance
   unrecoverable after resume.
2. **B — A + records** (G3, G4): extends the 2026-11-05 pattern. Recovers tool
   cards and compaction, but steering still degrades to a plain user row.
3. **C — B + steering field** (chosen): one optional message field closes the
   last provenance gap. It crosses the "don't touch `Message.to_json()`"
   scope fence deliberately, under the rules in *Compatibility*.

C costs roughly half a day more than B and removes the only gap that cannot
be reconstructed from existing data.

## The contract

### 1. View ids (G1)

`MessageView.id` and `ToolCallView.id` are strings of the form `v{n}`
(1-based position in the view list), assigned by both `messages_to_views()`
and `TranscriptBuilder` when a view is appended.

- Tool cards keep `call_id`; `id` exists for row identity only.
- Ids are assigned at append, so removing a row (see 1b) leaves a gap. The
  invariant is **reload determinism**: loading the same file twice yields the
  same ids, and rows keep their identity across a snapshot refresh.
- A rewrite/fork/compact is a new file, hence a new id space; the GUI reloads
  the whole snapshot on those operations anyway.
- Why not message UUIDs: identity is only needed within one append-only
  session, and ordinals provide it without changing the message vocabulary.

### 1b. Live/replay row parity (G1)

`messages_to_views()` drops an assistant turn with no text and no thinking;
the live builder keeps an empty `MessageView` created at `message_start`. A
tool-call-only turn therefore shows a permanent empty row, and the two paths
disagree on the row sequence.

Fix: at `message_end` (and in `finalize()`) the builder drops the current
assistant view when it ends with neither text nor thinking. The tool cards are
that turn's visible content. Frontends apply the same rule: a `message_end`
whose canonical message has empty text and thinking removes its streaming row.
Thinking-only turns are kept.

### 2. Metadata projection (G2)

`MessageView` gains:

| field | source | notes |
|---|---|---|
| `id` | append order | see above |
| `timestamp` | `UserMessage.timestamp` / `AssistantMessage.timestamp` | epoch seconds, already persisted |
| `model`, `provider` | `AssistantMessage` | show model switches / per-row badges |
| `usage` | `AssistantMessage.usage` | per-message usage, not the per-turn delta row |
| `duration_ms` | `AssistantMessage.duration_ms` | response duration |
| `steering` | `UserMessage.steering` | see G6 |

`thinking_seconds` remains live-only: no durable source exists, and inventing
one is out of scope. History rows simply show no reasoning timer.

### 3. `tool_end` records (G3)

On `tool_execution_end`, `ChatController` appends one record to the live
session (same best-effort path as `SessionMetaRecorder`):

```json
{"type": "tool_end", "timestamp": 1762…, "call_id": "call_…",
 "tool_name": "edit_file", "duration_ms": 812, "is_error": false,
 "details": {"diff": "…", "exit_code": 0}}
```

- `details` is bounded when serialized (target: 16 KiB); `diff` is truncated
  at a line cap before writing.
- Hydration: `TranscriptBuilder.load(messages, records)` applies records after
  pairing calls with results, last record wins per `call_id`. A record with no
  matching card (compacted or torn history) is ignored.
- **Rewrite carry**: `Session.carry_records(kept_messages)` copies `tool_end`
  records whose `call_id` still appears in the kept messages, plus
  `compaction` records. `run_start`/`run_end`/`retry`/`mode` are dropped: a
  truncated file must not claim run boundaries whose messages are gone. Apply
  in `Session.rewrite`, `Session.fork_at`, `SessionManager.fork`,
  `prepare_retry`, compact and shake.
- **Unpaired calls**: history replay currently leaves `status="running"` for a
  call with no result, so a resumed session spins forever. Loading marks such
  a call `status="interrupted"` (new literal) and leaves `result_text` empty;
  the session was not running when it was loaded.
- Why a record instead of a `ToolResultMessage` field: the message line stays
  the model vocabulary, and details remain explicitly UI-only. This is the
  same seam yesterday's records use.

### 4. `compaction` records (G4)

`compact()` writes one record into the new file, after the header and before
the summary messages:

```json
{"type": "compaction", "timestamp": 1762…, "tokensBefore": 142300,
 "derivedFrom": "20261106-101500", "reason": "auto"}
```

`ChatController.compact_now()` supplies `tokensBefore` (already computed for
the notice) and `reason` (`auto` | `manual`).

**Wire shape and dedupe**: the `transcript` result gains a top-level
`compactions` array (records from the loaded session, oldest first). The
divider is rendered from that array, never synthesized from a notice.
`TranscriptNotice` gains an optional `source: str = ""`; the compaction
divider sets `source="compaction"`, and `_transcript_result` filters those out
of `notices`. The TUI keeps draining notices through `take_notices()` and is
unchanged; the RPC client therefore sees each divider exactly once, including
after a re-fetch.

### 5. Session list (G5)

`SessionInfo` / `SessionEntry` / the `session list` RPC result gain:

- `created_at`: header `createdAt`, falling back to mtime for legacy files.
- `updated_at`: the current `mtime` value (today's `created` field is really
  "last activity"; it is renamed in the Python entry and exposed under the new
  name — the wire has no timestamp field at all today, so this is additive).
- `model`: provider model id of the last assistant message, empty when none.
- `derived_from`: header `derivedFrom`, empty when the session is original.

Token totals and cost stay out of the list (see Non-goals).

### 6. Steering provenance (G6)

- `UserMessage` gains `steering: bool = False`.
- `to_json()` emits `"steering": true` **only when true**; normal user lines
  stay byte-identical.
- `message_from_json` accepts an optional boolean; anything other than `true`
  deserializes as `False`.
- `Agent._drain_steering()` sets it. Note that multiple queued lines aggregate
  into one delivered message; the flag is per delivered message, not per line.
- `serialize_event` → `_message_projection` adds `"steering": true` for the
  live `message_start`/`message_end` of such a row, so the GUI can style it
  and clear its steer chips without waiting for a reload.
- Views project it onto `MessageView.steering`.

## Compatibility

- Old files: missing `steering` means `False`; missing records mean current
  behavior; ids are synthesized at view time, so no migration exists.
- Normal messages serialize byte-identically (no `"steering": false`), so
  golden transcript tests stay green.
- Consumers that read only messages (`export`, `usage`, `compact`, `retry`,
  `fork`, external `jq`) are unaffected.
- The `steering` flag never reaches a provider: adapters build payloads from
  dataclasses, and no adapter reads the field.
- A session file written before `tool_end` existed loads with the old generic
  cards: no errors, just less detail, and unpaired calls show `interrupted`.

## Tests

- `tests/test_session.py`: `steering` round-trip, normal-line byte stability;
  `tool_end`/`compaction` records survive `load()`; `carry_records` keeps
  matching call ids and drops run/retry/mode; legacy file unchanged.
- `tests/test_app_views.py`: reload determinism (same ids twice); metadata
  projection; steering projection; empty assistant rows dropped at
  `message_end`/`finalize`; `load(messages, records)` hydrates cards; orphan
  records ignored; unpaired calls are `interrupted`.
- `tests/test_app_session_meta.py`: a drained steering message persists the
  flag; non-steering prompts do not.
- `tests/test_app_chat.py`: `tool_end` written on completion; `compact_now`
  writes `compaction` with `tokensBefore`; a fork/undo keeps matching
  `tool_end` records.
- `tests/test_cli.py`: `transcript` includes ids/metadata/`compactions` and no
  compaction notice; live `message_projection` carries `steering`; `session
  list` includes `created_at`/`updated_at`/`model`/`derived_from`.
- Golden JSON prompts unchanged.

## Risks and mitigation

- **Record growth** — bounded `details`, one record per tool call. The result
  text already dominates the file today.
- **Subscription ordering** — write from `ChatController._on_event`, which
  already resolves the live session, not from the RPC layer.
- **Divider duplication** — one source of truth: the record. Notices with
  `source="compaction"` are excluded from the snapshot.
- **Carry correctness** — filter by call ids present in the kept messages;
  never carry run boundaries into a truncated file.
- **Compaction ordering** — write the record before the summary; if the
  best-effort append fails, the divider is lost but the run is not.

## Left to the builder

- Exact serialized cap for `details` and the diff truncation policy.
- CHANGELOG wording and the `docs/guide/sessions.md` additions.
