# Session Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt four dsh-inspired session-design ideas into zen-agent without giving up the plain-JSONL, human-readable transcript philosophy: semantic crash repair on resume, content-addressed blob offload for images, delegation depth stamped into subagent transcripts, and a listing index cache plus fail-closed incremental persistence.

**Architecture:** All changes respect the existing kernel/app layering: the kernel `Session` (zenagent/agent/session.py) stays record-kind-ignorant — new behavior arrives as injectable callables (blob store) or app-layer helpers (repair, index cache) wired through `records.open_session` / `list_sessions` / the bootstrap persist subscriber. Nothing rewrites existing transcript lines; every new disk format is backward compatible (old readers ignore unknown keys, new readers tolerate absent keys).

**Tech Stack:** Python >= 3.11, stdlib only (hashlib/shutil/json/atomic os.replace), pytest + pytest-asyncio (auto mode), ruff + mypy (ratcheted via scripts/check.sh).

**Spec:** This conversation's analysis (2026-11-17): the four adoption rows of the zen/pi/dsh comparison — (1) synthesize "outcome unknown" tool results on resume, (2) images out of the JSONL via content-addressed store, (3) persist delegation depth so a resumed child never resets to top-level, (4) listing projection cache + durable-before-side-effects persistence ordering.

## Global Constraints

- Python >= 3.11; stdlib only — no new runtime dependencies (pyproject.toml `requires-python = ">=3.11"`).
- Transcripts stay append-only: no task may rewrite or truncate an existing session line; the only file-writing primitives are `Session._write_line`-style append, and atomic tmp+`os.replace` for sidecars/blobs.
- Kernel stays record-kind-ignorant: `zenagent/agent/session.py` must not import `zenagent.app.records` or learn new record types; app behavior is injected (same pattern as `boundary`/`window`).
- Old files must behave identically: inline images, depth-less task meta, and cache-less sessions dir all work exactly as before.
- Model-facing strings are plain English (precedent: `LOOP_GUARD_TEXT`); no i18n `t()` for synthesized tool results.
- Checks: `uv run pytest tests/ -q`, `uv run ruff check .`, `uv run mypy zenagent` (new modules must be clean; scripts/check.sh is the ratchet).
- One commit per task, conventional-commit style, matching repo voice (short imperative subject).

## Review Focus

Five input classes the spec implies but individual tasks may not exercise — each pinned by a test in the owning task:

1. **Torn tail after a dangling assistant turn** (crash mid tool-run writes half a result line): repair must fire on the truncated view, and blob hydration must not crash on the torn line. → Task 1 test `test_repair_after_torn_tail_line`; Task 1 own torn-line test doubles for load safety.
2. **Old session with inline images reopened** (no `blob` key anywhere): load must return identical messages; no `_blobs` dir is created on read. → Task 2 `test_inline_images_load_unchanged_and_no_dir_created`.
3. **Blob file deleted out-of-band** (user cleanses `_blobs/`): load degrades to empty image data with a warning, never raises. → Task 2 `test_missing_blob_degrades_to_empty_data_with_warning`.
4. **Mid-run flush failure** (disk full at the new message_end checkpoint): pending messages survive to the agent_end backstop and still land. → Task 5 `test_message_end_flush_failure_keeps_agent_end_backstop`.
5. **Repair must never touch the listing path**: `list_sessions`/`read_info` on a dangling-call file must not append synthesized rows. → Task 1 `test_repair_does_not_run_on_listing_paths`.

---

### Task 1: Semantic crash repair on resume

**Files:**
- Modify: `zenagent/app/records.py` (add `repair_interrupted_run` after `keep_for_rewrite`)
- Modify: `zenagent/app/runtime.py:741-768` (`_switch_into`) and `zenagent/app/bootstrap.py:338-340` (the `agent.replace_messages(runtime.session.messages)` resume block)
- Test: `tests/test_resume_repair.py` (new)

**Interfaces:**
- Consumes: `Session.append` (async, existing), `AssistantMessage`/`ToolCall`/`ToolResultMessage`/`TextContent` from `zenagent.types`, `records.open_session` projection.
- Produces: `async def repair_interrupted_run(session: Session, messages: list[Message]) -> list[Message]` — returns `messages` extended with synthesized results; appends each synthesized result to `session`. Task 1's wiring callers are the only consumers.

**Design pinned:**
- Scan only the **last** `AssistantMessage` in `messages` (crash windows can only dangle the final turn: append-only flow always answers earlier turns before the next one starts; a torn tail can only cut the end).
- A call is unanswered when no `ToolResultMessage` with its `tool_call_id` appears anywhere after that assistant message. Skip the empty placeholder call (`id == "" and name == ""`, loop.py:310's filter).
- Synthesize, in the assistant message's source order, one `ToolResultMessage(tool_call_id, tool_name, content=[TextContent(SYNTHESIZED_RESULT_TEXT)], is_error=True, timestamp=int(time.time()))` per unanswered call, appended to the session via `await session.append(...)` (order preserved by the per-session lock).
- `SYNTHESIZED_RESULT_TEXT = ("This tool call never completed: the session was interrupted before its result was recorded. Its outcome is unknown — inspect the current state before re-issuing the call.")` — module constant in records.py.
- No-op paths (return `messages` untouched, write nothing): empty messages, no assistant message, no calls, all calls answered, or the session file does not exist (`session.lazy` and `not session.path.exists()`).
- Interaction with `providers/history.py replayable` (document in the docstring): calls of failed/aborted turns are still dropped from requests by `_FAILED_STOP_REASONS`; the synthesized results make the *file and display* self-consistent and inform the model only for healthy `tool_use` turns cut by a torn tail — exactly the crash case.

- [ ] **Step 1: Write the failing tests**

`tests/test_resume_repair.py`, pytest-asyncio auto mode, real `Session` objects in `tmp_path` (mirror `tests/test_session.py` fixtures):

```python
async def test_repair_synthesizes_results_for_dangling_calls(tmp_path):
    # session file whose last assistant message carries two tool calls,
    # followed by a result for only the first; assert repair appends one
    # ToolResultMessage for the second call with is_error=True, the exact
    # SYNTHESIZED_RESULT_TEXT, and returns the extended list.

async def test_repair_noop_cases(tmp_path):
    # empty messages; transcript without any assistant message; last
    # assistant without calls; all calls answered; lazy session with no
    # file — each returns the same list object/contents and writes nothing.

async def test_repair_does_not_run_on_listing_paths(tmp_path):
    # a dangling-call file: Session(path).load() and list_sessions() leave
    # the file byte-identical (mtime/size unchanged).

async def test_repair_after_torn_tail_line(tmp_path):
    # same as the first test but the unanswered result line exists as a
    # half-written JSON fragment at EOF; load() skips it, repair fires,
    # and a reload replays assistant + synthesized result cleanly.

async def test_switch_session_runs_repair(tmp_path):
    # runtime-level: build the dangling file, runtime.switch_session(path),
    # assert agent.messages ends with the synthesized ToolResultMessage.
```

Exact assertions: `is_error is True`, `content == [TextContent(text=SYNTHESIZED_RESULT_TEXT)]`, `tool_call_id` matches the dangling call, appended in source order.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resume_repair.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'repair_interrupted_run'` (import error).

- [ ] **Step 3: Implement `repair_interrupted_run` in `zenagent/app/records.py`**

Signature above; export in `__all__`. Pure scan + synthesize; all session writes through `await session.append`. Then wire: in `_switch_into` (runtime.py) insert `messages = await records.repair_interrupted_run(session, messages)` between `messages = session.load()` and `self.agent.replace_messages(messages)`; in bootstrap.py's resume block call it with `runtime.session` before `agent.replace_messages`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_resume_repair.py tests/test_session.py tests/test_app_runtime.py -q`
Expected: PASS (existing session/runtime suites unaffected).

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/records.py zenagent/app/runtime.py zenagent/app/bootstrap.py tests/test_resume_repair.py
git commit -m "feat(session): synthesize unknown-outcome results for dangling calls on resume"
```

---

### Task 2: Content-addressed blob offload for images

**Files:**
- Create: `zenagent/agent/blob_store.py`
- Modify: `zenagent/agent/session.py` (new `blob_store` field on `Session`, offload in `_append_sync`, hydrate in `load`)
- Modify: `zenagent/types.py:250-253` (image `message_from_json` branch: tolerate absent `data`)
- Modify: `zenagent/app/records.py:249-270` (`open_session` passes a store)
- Test: `tests/test_blob_store.py` (new)

**Interfaces:**
- Produces: `BLOB_MIN_CHARS = 4096`; `def blob_root_for(session_path: Path) -> Path` (= `session_path.parent / "_blobs"`); `class BlobStore: __init__(root: Path)`, `def offload(self, payload: dict) -> dict`, `def hydrate(self, payload: dict) -> dict`. `Session` gains field `blob_store: BlobStore | None = None` (dataclass, `repr=False, compare=False`).
- Consumes: `ImageContent.to_json()` shape `{"type": "image", "data": str, "mime_type": str}`; `Session._write_line`/`load` line loop; `append_line` (agent/jsonl.py).

**Design pinned:**
- `offload` walks only top-level message dicts' content lists (`payload["content"]` when it is a list): any `{"type": "image"}` block whose `data` is a `str` with `len(data) >= BLOB_MIN_CHARS` gets `data` removed and `blob: sha256(data).hexdigest()` added. Blob file `<root>/<sha>.b64` holds the base64 text verbatim, written tmp+`os.replace`, only when absent. Any `OSError` → return the payload unchanged (inline fallback, artifacts.py philosophy).
- `hydrate` is the mirror: block with `"blob"` and no usable `data` → read `<root>/<blob>.b64`; success restores `data`; failure leaves `data` absent (the from_json tolerance turns it into `""`) and returns a flag the caller turns into a `Session.warnings` entry (`f"{path.name}: missing image blob {ref}"`).
- Wire: `_append_sync` calls `self.blob_store.offload(message.to_json())` when the store is set; `load()` calls `hydrate` on every parsed message dict **unconditionally, constructing the store from `blob_root_for(self.path)` when not injected** — bare `Session(path)` readers (export, listings, `reload_transcript`'s display load) stay lossless with zero wiring.
- `message_from_json` image branch: `data = d.get("data") or ""` instead of `_require(d, "data", ...)`.
- `open_session` constructs `BlobStore(blob_root_for(path))` and passes it as `blob_store=`.
- Off-disk keys never collide with sessions: `_blobs` starts with `_`, and `list_sessions` globs `*.jsonl` only.
- Fork/undo `rewrite`/`_append_sync` re-offload: sha-addressed, so the same bytes never duplicate.

- [ ] **Step 1: Write the failing tests**

```python
def test_roundtrip_offloads_and_hydrates(tmp_path): ...
    # ImageContent with 8192-char data: append via a Session wired with
    # BlobStore; assert the file line has "blob" and no "data", _blobs/<sha>.b64
    # exists; load() returns the original data.

def test_inline_images_load_unchanged_and_no_dir_created(tmp_path): ...
    # legacy file with inline image (hand-written line): bare Session (no
    # store) load returns identical data; assert not (tmp_path/"_blobs").exists().

def test_missing_blob_degrades_to_empty_data_with_warning(tmp_path): ...
    # line with blob ref, _blobs file deleted: load gives ImageContent(data="")
    # and a warnings entry naming the ref; no exception.

def test_small_images_stay_inline(tmp_path): ...
    # data below BLOB_MIN_CHARS: file line keeps inline data; no _blobs dir.

def test_dedup_across_appends(tmp_path): ...
    # same image appended to two sessions in one dir: exactly one blob file.

def test_open_session_wires_the_store(tmp_path): ...
    # records.open_session(path) + append of a big image: file line carries blob.

def test_offload_failure_falls_back_inline(tmp_path, monkeypatch): ...
    # make os.replace raise OSError inside BlobStore: append still writes the
    # full inline line.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_blob_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zenagent.agent.blob_store'`.

- [ ] **Step 3: Implement `blob_store.py`, then the three integration edits**

New module per Interfaces block. `Session` edits: add field (after `lazy`), offload call in `_append_sync` before `_write_line`, hydrate call in `load`'s message branch before `message_from_json` (warning on missing). `types.py` one-line tolerance. `records.open_session` one-line wiring. Docstrings follow artifacts.py's voice (best effort / atomic / content-addressed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_blob_store.py tests/test_session.py tests/test_app_records.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/agent/blob_store.py zenagent/agent/session.py zenagent/types.py zenagent/app/records.py tests/test_blob_store.py
git commit -m "feat(session): spill large image payloads to a content-addressed blob store"
```

---

### Task 3: Delegation depth in subagent transcripts

**Files:**
- Modify: `zenagent/agent/subagents.py:196-233` (`SubagentStore.begin`) and the facts/parse side (`_parse_transcript` ~line 398, `SubagentFacts` ~line 152)
- Modify: `zenagent/agent/subagents_runtime.py` (`_TaskRecorder` gains and forwards `delegation_depth`; `TaskManager` tracks active nesting)
- Test: `tests/test_subagent_depth.py` (new)

**Interfaces:**
- Produces: `SubagentStore.begin(self, call_id: str, name: str, model: str, *, delegation_depth: int = 0)` — writes `"delegationDepth": n` into the meta header line only when `n > 0`; `SubagentFacts.delegation_depth: int = 0` read back by the transcript parser; module-level `contextvars.ContextVar[int]` named `_delegation_depth` in subagents_runtime (default 0).
- Consumes: existing `TaskManager` run path and `_TaskRecorder.start()` → `store.begin(...)`.

**Design pinned:**
- Depth convention: main session = 0 (implicit, never stamped); a task spawned by the main runtime = 1; a task spawned from inside a task body = 2; and so on. This is the zen-agent equivalent of dsh's `delegationDepth` — here child sessions are task transcripts, not main Sessions, so the main `Session` header is deliberately left untouched.
- `TaskManager.run` records `depth = _delegation_depth.get() + 1` at spawn, passes it to `_TaskRecorder(delegation_depth=depth)`, and runs the task body with `_delegation_depth.set(depth)` so nested spawns compound. A plain `contextvars.ContextVar` (not an int counter) keeps concurrent tasks isolated under asyncio.
- Parser tolerance: existing transcripts without the key read as depth 0 (the `int(raw.get("delegationDepth") or 0)` pattern already used by swarm/room.py:253).
- Rationale comment (docstring, quoting the dsh decision): "a runtime-only depth would reset a resumed child to top-level".

- [ ] **Step 1: Write the failing tests**

```python
def test_begin_stamps_depth_into_meta(tmp_path): ...
    # SubagentStore with a directory: begin(..., delegation_depth=2) →
    # first line JSON has delegationDepth == 2; begin without it → key absent.

def test_facts_read_back_depth(tmp_path): ...
    # write a transcript via begin/append/finish with depth 2; parse side
    # returns SubagentFacts(delegation_depth=2); legacy file without the key
    # parses as 0.

async def test_nested_spawn_compounds_depth(...): ...
    # drive TaskManager the way existing subagent_runtime tests do (find the
    # fixture pattern in tests/test_subagents*.py): spawn a task whose body
    # spawns another; assert outer transcript depth 1, inner depth 2.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_subagent_depth.py -v`
Expected: FAIL with `TypeError: begin() got an unexpected keyword argument 'delegation_depth'`.

- [ ] **Step 3: Implement the store field, recorder forwarding, and the ContextVar**

Follow the Interfaces block; the parse side mirrors room.py's tolerant `int(raw.get(...) or 0)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_subagent_depth.py tests/ -q -k "subagent or task"`
Expected: PASS across the subagent/task suites.

- [ ] **Step 5: Commit**

```bash
git add zenagent/agent/subagents.py zenagent/agent/subagents_runtime.py tests/test_subagent_depth.py
git commit -m "feat(subagents): stamp delegation depth into task transcript headers"
```

---

### Task 4: Listing index cache

**Files:**
- Create: `zenagent/agent/session_index.py`
- Modify: `zenagent/agent/session.py:808-847` (`list_sessions`)
- Test: `tests/test_session_index.py` (new)

**Interfaces:**
- Produces: `class SessionIndex: __init__(self, directory: Path)`, `def read(self, path: Path, titles: dict[str, str]) -> SessionInfo` (raises the same `ValueError`/`OSError` `read_info` would on unreadable files — never cached), `def flush(self) -> None`. Sidecar: `<directory>/_index.json`, mapping `filename -> {"size": int, "mtime_ns": int, "messages": int, "title": str, "created_at": float, "model": str, "started": bool}`.
- Consumes: `Session.read_info(titles=...)`, `read_session_titles`.

**Design pinned:**
- Invalidation: cache entry used only when the file's `stat().st_size` and `stat().st_mtime_ns` match exactly; any mismatch or missing entry → `Session(path).read_info(titles=titles)`, then record. Known limit (docstring): a rewrite that lands in the same nanosecond with the same size is indistinguishable — accepted, the cache is advisory.
- `read` applies the titles override **fresh on every call** (the cache stores only the derived brief under `title`, exactly what `_title_from_messages` produced; overrides live in titles.json and may change independently).
- `flush` is tmp+`os.replace`, swallows `OSError`, merges over the on-disk map (two processes listing concurrently: last writer wins, entries revalidated by stat anyway). Corrupt `_index.json` → treated as empty.
- `list_sessions` wiring: per directory, `index = SessionIndex(directory)`; loop body replaces `Session(path).read_info(titles=titles)` with `index.read(path, titles)`; `index.flush()` after the directory's loop. Everything else (pins filter, `(empty)` skip, sort, project label) untouched.
- Unreadable files are not cached: `read` lets `ValueError` propagate without recording.

- [ ] **Step 1: Write the failing tests**

```python
def test_second_listing_hits_cache(tmp_path, monkeypatch): ...
    # two sessions; count Session.read_info calls via monkeypatch; first
    # list_sessions → N calls, second → 0; SessionInfo fields identical.

def test_append_invalidates_entry(tmp_path, monkeypatch): ...
    # list; append a message to one file; list again → read_info called once
    # for that file, results reflect the new count.

def test_corrupt_index_tolerated(tmp_path): ...
    # _index.json = "{not json"; listing still works and rewrites a valid one.

def test_flush_failure_swallowed(tmp_path, monkeypatch): ...
    # make os.replace raise; list_sessions returns correct infos.

def test_titles_override_applied_fresh(tmp_path): ...
    # cached entry; change titles.json; next read shows the override without
    # read_info being re-invoked.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zenagent.agent.session_index'`.

- [ ] **Step 3: Implement `session_index.py` and rewire `list_sessions`**

Per Interfaces; keep the module under 120 lines, artifacts.py docstring voice.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_index.py tests/test_session.py tests/test_cli_list_sessions.py -q` (adjust the third name to the actual sessions-listing test file if different)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/agent/session_index.py zenagent/agent/session.py tests/test_session_index.py
git commit -m "feat(session): cache listing metadata in a per-directory index"
```

---

### Task 5: Fail-closed incremental persistence

**Files:**
- Modify: `zenagent/app/bootstrap.py:306-322` (the `persist` subscriber)
- Test: `tests/test_incremental_persist.py` (new)

**Interfaces:**
- Consumes: `runtime._unpersisted_messages` + `runtime._flush_unpersisted(session) -> bool` (runtime.py:549, existing rewind flush; keeps the unsent tail on failure), subscription order `_record_unpersisted` **before** `persist` (bootstrap.py already subscribes in that order — do not reorder).
- Produces: no new symbols; behavior change only. The dsh checkpoints this buys: assistant turn durable before tool bodies run (loop.py:299 `message_end` precedes `tool_execution_start`), tool results durable before the next model request.

**Design pinned:**
- The `persist` subscriber becomes: on `message_end` **and** on `agent_end`, drain `runtime._unpersisted_messages` via `await runtime._flush_unpersisted(runtime.session)`. Per-message cost is unchanged — `agent_end` already called `session.append` once per message; the flush just happens earlier, and `Session.append`'s per-session lock keeps submission order.
- `agent_end` stays as the backstop: aborts and provider errors can skip `message_end` (chat.py:738), and a failed mid-run flush keeps its tail pending (runtime.py:565) for the backstop to retry.
- A flush `OSError` routes through the existing `note_persist_failure` path — reuse it, do not add a new failure channel.
- Metrics flush stays on `agent_end` only.

- [ ] **Step 1: Write the failing tests**

Use the fake-stream harness pattern from `tests/test_app_runtime.py` (drive one run that emits user → assistant-with-tool-call → tool result → agent_end):

```python
async def test_assistant_message_durable_before_tool_executes(...): ...
    # during the run, after the assistant message_end event and before the
    # tool result lands, assert the session file on disk already contains the
    # assistant line (read the JSONL mid-run from a subscriber).

async def test_message_end_flush_failure_keeps_agent_end_backstop(...): ...
    # session.append raises OSError on the first message_end flush only;
    # run completes; file contains all messages; note_persist_failure fired
    # at most once (or not at all if backstop succeeded silently — assert the
    # file, not the notice).
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_incremental_persist.py -v`
Expected: first test FAIL (mid-run file lacks the assistant line); second may PASS already (backstop) — if so keep it as a regression pin and note that in the commit body.

- [ ] **Step 3: Change the `persist` subscriber**

One edit in bootstrap.py per Design; docstring updated to state the two dsh checkpoints.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_incremental_persist.py tests/test_app_runtime.py tests/test_rewind.py tests/test_app_session_meta.py -q`
Expected: PASS (rewind flush and run-record ordering unchanged).

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/bootstrap.py tests/test_incremental_persist.py
git commit -m "feat(session): persist each message at message_end (durable before side effects)"
```

---

### Task 6: Full gate and changelog

**Files:**
- Modify: `CHANGELOG.md` (one entry summarizing the four features)

- [ ] **Step 1: Full check gate**

Run: `uv run pytest tests/ -q --timeout=120`
Expected: 3122+ new tests, 0 failures.

Run: `uv run ruff check . && uv run mypy zenagent`
Expected: clean (new modules must be mypy-clean; if a touched module is on the frozen ratchet list, keep its error count from rising — scripts/check.sh is authoritative).

- [ ] **Step 2: CHANGELOG entry + commit**

Unreleased-style entry at the top matching the file's existing voice; commit `changelog: session hardening — crash repair, blob offload, delegation depth, listing cache, incremental persist`.

- [ ] **Step 3: Report**

Report the branch summary to the user; leave merging to them (finishing-a-development-branch is a separate decision).
