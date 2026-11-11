# Zen × Swarm Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the correctness, contract, permission and lifecycle findings from the post-merge zen×swarm code review, in four risk-ordered batches, without regressing the merged features.

**Architecture:** Fixes land in the existing kernels (`app/swarm`, `app/zen`, `cli/rpc`, `desktop/src`) — no new subsystem. The one structural change is extracting the swarm frame-emission helpers into a `SwarmFrames` mixin (`app/swarm/frames.py`) to create LOC headroom under the 800-line architecture cap before the orchestration hardening lands. Permission contagion is fixed at the fleet boundary (workers downgrade `zen`→`ask`); stop-intent is an in-memory desktop flag honored by the re-sync path.

**Tech Stack:** Python 3 (zero third-party deps beyond httpx), pytest, ruff; TypeScript + React + zustand, `node --test` (native TS stripping), Playwright e2e; Tauri desktop shell.

**Spec:** `/Users/mac/.zenagent/reports/zen-swarm-merge-review-20261111/` (the review report; `04-correctness.md` carries P0 #1–#5, P1 #6–#11, P3 mock差集). Binding design authority for permission semantics: `docs/superpowers/specs/2026-11-10-zen-mode-design.md` (approvals must surface to the UI; `[zen] max_concurrent` configurable). The report is a snapshot @5a84f90 (8 commits behind main); each task re-verifies its site against the current tree before editing.

## Global Constraints

- All work in worktree `.worktrees/fix-zen-swarm-review` on branch `fix/zen-swarm-review` (base `main@16edfbd`). Never edit the `main` checkout. Never push without an explicit request.
- Single CI gate: `bash scripts/check.sh` = `uv sync --extra cli --dev` → `uv run ruff check .` → `python3 scripts/check_architecture.py` → `uv run pytest -q`. Every task ends green on the relevant subset; the branch ends green on the full gate.
- Architecture LOC cap `DEFAULT_CAP = 800`; allowlist (`docs/architecture/allowlist.json`) only shrinks. `zenagent/app/swarm/manager.py` is **exactly 800** and **not** allowlisted — any net addition fails the gate. New `app/swarm/frames.py` must stay well under 800.
- `PROTOCOL_VERSION = 3` on both sides (`zenagent/cli/rpc`, `desktop/src/rpc/types.ts`); pinned by `tests/test_rpc_contract.py`.
- Zero cross-import between `app/zen` and `app/swarm`; plugins call outward only; credentials live only in `~/.zenagent/settings.json`, role manifests carry `${KEY}` placeholders only.
- Desktop unit tests: `cd desktop && npm test` (`node --test tests/*.test.ts`); single file `node --test tests/<name>.test.ts`. e2e: `cd desktop && npm run e2e` (`playwright test`); single spec `npx playwright test e2e/<name>.spec.ts`.
- i18n zh/en key parity is pinned by `desktop/tests/locale.test.ts` — every new key lands in both locales in `desktop/src/i18n/messages.ts`.
- CHANGELOG: released entries stay verbatim; the **Unreleased** "Zen 模式 × Swarm" section may be corrected in place.
- Python single test: `uv run pytest tests/<file>.py::<name> -q`.

## Review Focus

The five input classes most likely to bite a real user, each pinned to its owning task's tests:

1. **A role manifest pointing outside its directory** — `systemPromptFile: "../../secret"` *and* a `skills: ["../evil"]` entry must both be refused, not just the prompt. → T2 tests both vectors.
2. **A crash mid-append leaving an unterminated JSONL tail** — including a tail that is *valid JSON but has no trailing newline*; the next append must not concatenate onto it and load must repair it. → T3 tests the unterminated-but-parseable case, not only malformed JSON.
3. **Stop then navigate** — user stops the fleet, then switches project (re-sync) or the app re-syncs; the fleet must stay stopped, yet a fresh boot must still auto-arm a persisted zen toggle. → T6 tests both the persist-across-switch and re-arm-on-fresh-store boundaries.
4. **A worker running a destructive tool under zen mode** — must surface an approval and honor a *denial*, not auto-run. → T7 pins the downgrade; T14 pins the deny branch end-to-end.
5. **Close/delete while work is in flight** — closing the manager with a pending approval future must fail it promptly (no 120 s stall); deleting a role mid-run with a pending config edit must leak no runtime and lose no `pending_apply`. → T11 (delete race + drain) and T13 (close fails futures).

---

## Batch ① — Contract surface + cheap hardening

### Task 1: Wire-contract pins + `swarm_notice` carries `swarm:true`

**Files:**
- Modify: `zenagent/app/swarm/manager.py:656-662` (the `_admit` notice frame)
- Modify: `tests/test_rpc_contract.py` (add two tests)
- Create: `desktop/tests/bridge.test.ts` (headless bridge harness + hello regression)

**Interfaces:**
- Consumes: `_rpc_source()` and `TS_PATH` already in `test_rpc_contract.py`; `installBridge(state)` / `initialState(overrides)` from `desktop/src/dev/bridge.ts`; `PROTOCOL_VERSION` from `desktop/src/rpc/types.ts`.
- Produces: a `bridge.test.ts` harness `bootBridge(overrides?)` returning `{ invoke(payload), state }` that T9 extends; the canonical server-frame wire-type set asserted equal to the TS `ServerFrame` union.

- [ ] **Step 1: Write the failing backend tests** in `tests/test_rpc_contract.py`:

```python
SERVER_FRAME_TYPES = {
    "result", "error", "event", "zen_fleet",
    "swarm_message", "swarm_task", "swarm_agent", "swarm_notice", "swarm_event",
}

def _backend_source() -> str:
    pkg = Path(__file__).resolve().parents[1] / "zenagent"
    parts = [RPC_PATH]
    for rel in ("app/swarm/manager.py", "app/zen/fleet.py",
                "cli/rpc/ops_zen.py", "cli/rpc/ops_swarm.py"):
        parts.append((pkg / rel).read_text())
    return "\n".join(parts)

def test_server_frame_union_matches_backend_literals():
    src = TS_PATH.read_text()
    # every `type: '<lit>'` inside the ServerFrame member interfaces
    ts = set(re.findall(r"type:\s*'([a-z_]+)'", src))
    assert SERVER_FRAME_TYPES <= ts, f"TS union missing {sorted(SERVER_FRAME_TYPES - ts)}"
    backend = _backend_source()
    for lit in SERVER_FRAME_TYPES:
        assert f'"type": "{lit}"' in backend, f"backend never emits {lit}"

def test_swarm_frames_carry_swarm_flag():
    # each swarm_* emission site must set swarm=True in the same frame dict
    backend = _backend_source()
    for lit in ("swarm_message", "swarm_task", "swarm_agent", "swarm_notice", "swarm_event"):
        for m in re.finditer(rf'"type": "{lit}"', backend):
            window = backend[m.start(): m.start() + 240]
            assert "swarm=True" in window or '"swarm": True' in window, f"{lit} missing swarm flag"
```

Note: `swarm_event` is emitted in `ops_swarm.py` via a dict literal with `"swarm": True`; the frame-helper sites use `record.update(..., swarm=True)`. If a legitimate emission falls outside the 240-char window, widen the window rather than weakening the assertion.

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_rpc_contract.py::test_swarm_frames_carry_swarm_flag -q`
Expected: FAIL on `swarm_notice missing swarm flag` (the `_admit` notice at manager.py:656 omits it).

- [ ] **Step 3: Add `swarm:true` to the notice, net-zero LOC** in `manager.py:656-662`. Collapse the multi-line dict so the added key costs no net line (manager.py is at the 800 cap):

```python
self._write({
    "type": "swarm_notice", "swarm": True, "agent": message.author_id,
    "text": f"@{message.author_id}: {message.text[:280]}",
})
```

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/test_rpc_contract.py -q`
Expected: PASS (both new tests + the existing contract tests).

- [ ] **Step 5: Write the failing bridge hello regression** in `desktop/tests/bridge.test.ts`:

```typescript
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { installBridge, initialState, type BridgeState } from '../src/dev/bridge.ts'
import { PROTOCOL_VERSION } from '../src/rpc/types.ts'

function bootBridge(overrides: Partial<BridgeState> = {}) {
  const state = initialState(overrides)
  ;(globalThis as any).window = {}
  installBridge(state)
  const internals = (globalThis as any).window.__TAURI_INTERNALS__
  return {
    state,
    invoke: (payload: Record<string, unknown>) =>
      internals.invoke('rpc_send', { payload }) as Promise<Record<string, unknown>>,
  }
}

test('bridge hello reports the pinned protocol version', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'hello', id: 'h1' })
  assert.equal(frame.protocol_version, PROTOCOL_VERSION)
  assert.equal(frame.ok, true)
})
```

- [ ] **Step 6: Run to verify it passes** (the bridge already uses `PROTOCOL_VERSION`; this pins the regression)

Run: `cd desktop && node --test tests/bridge.test.ts`
Expected: PASS. If it errors on a missing global rather than asserting, shim only what `installBridge` touches (`window`) — do not mock `respond`.

- [ ] **Step 7: Write the handler-coverage guard** (P3 extension trap: `swarmWiring.ts:28` consumes *any* `swarm:true` frame, so a new `swarm_*` type added to the union without a handler is silently swallowed). Extend `desktop/tests/swarmWiring.test.ts`:

```typescript
import { readFileSync } from 'node:fs'
test('every swarm_* frame type in the union has a handler', () => {
  const types = readFileSync(new URL('../src/rpc/types.ts', import.meta.url), 'utf8')
  const swarmTypes = [...types.matchAll(/type:\s*'(swarm_[a-z_]+)'/g)].map((m) => m[1])
  assert.ok(swarmTypes.length >= 5, 'union lost swarm_* members')
  const handled = ['../src/state/swarmStore.ts', '../src/state/swarmWiring.ts']
    .map((p) => readFileSync(new URL(p, import.meta.url), 'utf8')).join('\n')
  for (const t of swarmTypes) {
    assert.ok(handled.includes(`'${t}'`), `${t} has no handler — would be silently swallowed`)
  }
})
```

- [ ] **Step 8: Run to verify GREEN + commit**

Run: `cd desktop && node --test tests/swarmWiring.test.ts`
Expected: PASS (all five current types are handled).

```bash
git add zenagent/app/swarm/manager.py tests/test_rpc_contract.py desktop/tests/bridge.test.ts desktop/tests/swarmWiring.test.ts
git commit -m "fix(swarm): tag swarm_notice with swarm:true and pin the wire frame contract"
```

---

### Task 2: Role manifest path containment (prompt + skills)

**Files:**
- Modify: `zenagent/app/swarm/roles.py:121-135` (`parse_role`)
- Test: `tests/test_swarm_roles.py` (create if absent; else extend)

**Interfaces:**
- Consumes: `parse_role(role_dir: Path) -> tuple[RoleManifest | None, list[str]]`, `_ID_RE`, `DEFAULT_PROMPT_FILE`.
- Produces: containment refusal warnings; a prompt/skills path that resolves outside `role_dir` is never read.

- [ ] **Step 1: Write the failing tests** (tmp_path role dirs):

```python
def test_prompt_file_traversal_is_refused(tmp_path):
    role = tmp_path / "r"; role.mkdir()
    (tmp_path / "secret.txt").write_text("leak")
    (role / "manifest.json").write_text(json.dumps({"id": "r", "systemPromptFile": "../secret.txt"}))
    manifest, warnings = parse_role(role)
    assert manifest is not None
    assert manifest.system_prompt == ""
    assert any("prompt" in w for w in warnings)

def test_skills_traversal_is_refused(tmp_path):
    role = tmp_path / "r"; role.mkdir()
    outside = tmp_path / "evil"; outside.mkdir()
    (role / "manifest.json").write_text(json.dumps({"id": "r", "skills": ["../evil"]}))
    manifest, warnings = parse_role(role)
    assert manifest is not None
    assert manifest.skill_roots == []
    assert any("skills" in w for w in warnings)
```

(Confirm the exact `RoleManifest` field names — `system_prompt`, `skill_roots` — against roles.py before asserting; adjust to the real names.)

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_swarm_roles.py -q`
Expected: FAIL — the traversal prompt reads `leak`; the skills entry resolves to the outside dir.

- [ ] **Step 3: Implement containment** in `parse_role`. Add a module helper and gate both reads:

```python
def _within(role_dir: Path, candidate: Path) -> bool:
    base = role_dir.resolve()
    return candidate.resolve().is_relative_to(base)
```

For the prompt: resolve `role_dir / str(prompt_file)`; if not `_within`, set `system_prompt = ""` and append a warning naming the prompt, skipping the read. For each skills entry: resolve `role_dir / entry`; if not `_within`, append a warning naming the skills entry and `continue` (do not add to `skill_roots`). Use `Path.is_relative_to` (3.9+).

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/test_swarm_roles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/roles.py tests/test_swarm_roles.py
git commit -m "fix(swarm): contain role prompt and skills paths within the role directory"
```

---

### Task 3: Room log durability — fsync + torn-tail repair

**Files:**
- Modify: `zenagent/app/swarm/room.py:241-244` (`_append_line`), `SwarmStore.load` (`:99-108`)
- Test: `tests/test_swarm_room.py` (create if absent; else extend)

**Interfaces:**
- Consumes: `SwarmStore`, `_append_line(path, payload)`, `_replay(path, apply)`.
- Produces: durable appends (fsync); a load that truncates an unterminated tail before replay so the next append starts on a clean line boundary.

- [ ] **Step 1: Write the failing tests:**

```python
def test_append_fsyncs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    store = SwarmStore(tmp_path)
    store.append_message(_msg("m1"))   # use the real record builder / fixture
    assert calls, "append must fsync"

def test_load_truncates_unterminated_tail(tmp_path):
    path = tmp_path / "messages.jsonl"
    good = json.dumps({"type": "message", "version": RECORD_VERSION, "id": "m1", ...})
    path.write_text(good + "\n" + '{"type": "message", "id": "torn"')  # valid JSON, NO newline
    store = SwarmStore(tmp_path); store.load()
    assert path.read_text().endswith("\n")
    assert "torn" not in path.read_text()
    # a subsequent append lands on its own line, not concatenated onto the tail
    store.append_message(_msg("m2"))
    assert sum(1 for _ in path.read_text().splitlines()) == 2
```

(Build `good`/`_msg` from the real `_message_record` so the test exercises the actual schema, not a hand-rolled one.)

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_swarm_room.py -q`
Expected: FAIL — no fsync call; the torn tail survives load and the next append concatenates.

- [ ] **Step 3: Implement.** In `_append_line`, after `handle.flush()` add `os.fsync(handle.fileno())` (import `os`). In `load`, before `_replay`, repair each log file: read bytes; if non-empty and not ending in `b"\n"`, truncate to the last `b"\n"` boundary (or to zero if none), so an unterminated tail — parseable or not — is removed before the next append. Keep `_replay`'s malformed-line skip as the second line of defense.

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/test_swarm_room.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/room.py tests/test_swarm_room.py
git commit -m "fix(swarm): fsync room appends and repair an unterminated log tail on load"
```

---

### Task 4: Desktop cheap hardening — drawer failure copy + key, background-run whitelist

**Files:**
- Modify: `desktop/src/ui/WorkerDrawer.tsx:83` (failure copy), `:89` (row key)
- Modify: `desktop/src/i18n/messages.ts` (new `zen.drawerFailed` zh + en)
- Modify: `desktop/src/state/stores.ts:624` (`zenBackgroundRun` boolean whitelist)
- Test: `desktop/tests/settings.test.ts` (extend), `desktop/tests/locale.test.ts` (parity auto-covers)

**Interfaces:**
- Consumes: `load<T>(key, fallback)`, the `loadWorkspaceMode` whitelist pattern (`stores.ts:594-604`), `t(key)`.
- Produces: `zen.drawerFailed` copy; a persisted `zen.fleet.background` that falls back to `true` for any non-boolean.

- [ ] **Step 1: Write the failing settings test** in `desktop/tests/settings.test.ts`:

```typescript
test('zenBackgroundRun falls back to true for a foreign persisted value', () => {
  localStorage.setItem('zen.fleet.background', '"yes"')
  // re-read through the same loader the store uses
  assert.equal(loadZenBackgroundRun(), true)
})
```

(Extract a tiny `loadZenBackgroundRun()` helper in stores.ts mirroring `loadWorkspaceMode`, returning `load<unknown>(...) === false ? false : true` — i.e. only an explicit `false` disables; anything else defaults true. The test imports that helper.)

- [ ] **Step 2: Run to verify RED**

Run: `cd desktop && node --test tests/settings.test.ts`
Expected: FAIL — the raw `load('zen.fleet.background', true)` returns the string `"yes"` (truthy but not boolean) / helper missing.

- [ ] **Step 3: Implement.** Add `loadZenBackgroundRun()` and use it at `stores.ts:624`. In `WorkerDrawer.tsx:83` replace `t('zen.drawerClose')` with `t('zen.drawerFailed')`; add the key to both locales (zh: `加载失败`, en: `Failed to load`). At `:89` replace `key={index}` with a stable key derived from the row content (e.g. ``key={`${row.kind ?? 'tool'}-${row.name ?? row.text ?? index}`}``) so reordering does not reuse DOM nodes across distinct rows.

- [ ] **Step 4: Run to verify GREEN**

Run: `cd desktop && node --test tests/settings.test.ts tests/locale.test.ts`
Expected: PASS (locale parity holds with the new key in both languages).

- [ ] **Step 5: Commit**

```bash
git add desktop/src/ui/WorkerDrawer.tsx desktop/src/i18n/messages.ts desktop/src/state/stores.ts desktop/tests/settings.test.ts
git commit -m "fix(desktop): correct worker drawer failure copy and harden background-run persistence"
```

---

### Task 5: Docs catch-up — CHANGELOG placeholders + arch-gov registration

**Files:**
- Modify: `CHANGELOG.md:64` and `:106` (Unreleased swarm subsection placeholder copy)
- Modify: `docs/arch-gov/manifest.json` (add `app.zen` node + edges; add `ops_zen.py` to `cli.rpc`; add `events_tap.py`/`transcript.py` to `app.swarm`; bump `version`)
- Modify: `docs/arch-gov/deltas.md` (append `[v6]`)

**Interfaces:**
- Consumes: the real file lists of `app/zen`, `app/swarm`, `cli/rpc` (enumerate before editing).
- Produces: a manifest that maps every source file (no `gov_diff` unmapped), and an Unreleased changelog with no "placeholder for the other branch" self-contradiction.

- [ ] **Step 1: Fix the CHANGELOG placeholders.** Replace `:64` "ZEN is a placeholder for the other branch" and `:106` "ZEN renders a placeholder page for the other branch" with accurate descriptions of the merged zen mode (router + fleet, approvals surface to the UI). Released entries elsewhere stay verbatim.

- [ ] **Step 2: Register arch-gov.** Enumerate `zenagent/app/zen/*.py`, `zenagent/app/swarm/*.py`, `zenagent/cli/rpc/*.py`. Add an `app.zen` node (files + resp/symbols mirroring the `app.swarm` node style) with edges to the kernels it composes (`agent.rt`, `app.assembly`, `config`, `settings`, `types` — verify against imports). Add `ops_zen.py` to `cli.rpc` and a `cli.rpc → app.zen` edge. Add the missing `app/swarm/events_tap.py` and `transcript.py` to the `app.swarm` node. Bump manifest `version` 4→5 (or next free). Do **not** touch `brief.md` (its generator `scaffold.py` is absent from the repo).

- [ ] **Step 3: Append `deltas.md` `[v6]`** dated 2026-11-11 recording: zen node added, swarm/cli.rpc file lists completed, the review-fix branch's contract/permission/lifecycle changes summarized, baseline re-registered if `check_architecture` reports drift.

- [ ] **Step 4: Verify mapping is clean**

Run: `python3 scripts/check_architecture.py`
Expected: PASS, no unmapped-file errors. (If a `gov_diff` subcommand exists, run it; otherwise the budget check + a manual grep that every `app/zen` and `app/swarm` file appears in the manifest.)

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/arch-gov/manifest.json docs/arch-gov/deltas.md
git commit -m "docs: correct unreleased zen changelog and register zen/swarm in arch-gov"
```

---

## Batch ② — Stop intent (A1/A2)

### Task 6: Fleet stop persists until explicit zen re-entry

**Files:**
- Modify: `desktop/src/state/zenStore.ts` (add `stoppedByUser` + setter; clear in `reset`/on enable)
- Modify: `desktop/src/state/zenActions.ts:74-86` (`stopFleet`), `:56-71` (`switchZenPresence`), `:104-121` (`syncZenState`)
- Modify: `desktop/src/ui/ZenRoster.tsx` (paused banner + resume affordance)
- Modify: `desktop/src/i18n/messages.ts` (new `zen.paused*` keys, zh + en)
- Test: `desktop/tests/zenActions.test.ts` (add stopped-intent tests), `desktop/tests/zenStore.test.ts`

**Interfaces:**
- Consumes: `useZenStore`, `useSettingsStore.workspaceMode`, `switchZenPresence`, `applyToggleResult`.
- Produces: `ZenState.stoppedByUser: boolean`, `markStopped(): void`; `syncZenState` skips auto-enable while `stoppedByUser`; `switchZenPresence(get, true)` clears it.

Semantics (verified): `zenStore.reset()` is **not** called on boot or project-switch in `appStore.ts` (only `chatStore.reset` is). The store is fresh on each boot, so `stoppedByUser` defaults false → a persisted `workspaceMode==='zen'` still auto-arms on boot (`appStore.ts:239`). It persists across project switches (`appStore.ts:607` re-sync) until the user explicitly re-enters zen.

- [ ] **Step 1: Write the failing tests** in `desktop/tests/zenActions.test.ts` (the existing auto-enable test at :60 stays valid — fresh store, flag false):

```typescript
test('syncZenState does not auto-enable after the user stopped the fleet', async () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  useZenStore.getState().markStopped()
  const env = makeEnv((p) => (p.op === 'state' ? okState(false) : { type: 'result', ok: true }))
  await syncZenState(env.get)
  assert.ok(!env.sent.some((p) => p.op === 'enable'), 'no revival while stopped')
  assert.equal(useZenStore.getState().enabled, false)
})

test('explicit zen entry clears the stopped flag and enables', async () => {
  resetStores()
  useZenStore.getState().markStopped()
  const env = makeEnv(() => ({ type: 'result', ok: true, session: '/p/r.jsonl', roster: [] }))
  await switchZenPresence(env.get, true)
  assert.equal(useZenStore.getState().stoppedByUser, false)
  assert.equal(useZenStore.getState().enabled, true)
})

test('stopFleet marks the fleet stopped', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'result', ok: true, roster: [] }))
  await stopFleet(env.get)
  assert.equal(useZenStore.getState().stoppedByUser, true)
})
```

- [ ] **Step 2: Run to verify RED**

Run: `cd desktop && node --test tests/zenActions.test.ts`
Expected: FAIL — `markStopped` missing; syncZenState revives the stopped fleet.

- [ ] **Step 3: Implement the store flag.** In `zenStore.ts` add `stoppedByUser: boolean` (default false), `markStopped: () => set({ stoppedByUser: true })`; `applyRoster(enabled, roster)` also sets `stoppedByUser: false` when `enabled` is true; `reset()` sets `stoppedByUser: false`.

- [ ] **Step 4: Wire the actions.** `stopFleet`: after `applyToggleResult(get, false, frame)` call `useZenStore.getState().markStopped()`. `switchZenPresence(get, true)`: clear the flag at entry with `useZenStore.setState({ stoppedByUser: false })` (explicit re-entry is the only thing that re-arms auto-enable). `syncZenState:113`: change the guard to `workspaceMode === 'zen' && !enabled && !useZenStore.getState().stoppedByUser`.

- [ ] **Step 5: Run to verify GREEN**

Run: `cd desktop && node --test tests/zenActions.test.ts tests/zenStore.test.ts`
Expected: PASS.

- [ ] **Step 6: Surface the paused state.** In `ZenRoster.tsx`, when `stoppedByUser && workspaceMode==='zen'`, render a paused banner tagged `data-zen-paused` with a resume button (`data-zen-resume`) that calls `switchWorkspaceMode(get, 'zen')` (re-entry). Add `zen.paused` / `zen.pausedResume` keys to both locales. Keep the existing stop button (`data-zen-stop`). T10's e2e relies on `data-zen-paused` / `data-zen-resume`.

- [ ] **Step 7: Verify locale parity + commit**

Run: `cd desktop && node --test tests/locale.test.ts`
Expected: PASS.

```bash
git add desktop/src/state/zenStore.ts desktop/src/state/zenActions.ts desktop/src/ui/ZenRoster.tsx desktop/src/i18n/messages.ts desktop/tests/zenActions.test.ts desktop/tests/zenStore.test.ts
git commit -m "fix(desktop): honor fleet stop intent until explicit zen re-entry"
```

---

## Batch ③ — Permission contagion + config + mock fidelity (A6)

### Task 7: Worker permission boundary — zen downgrades to ask

**Files:**
- Modify: `zenagent/app/zen/fleet.py:254-262` (`_build_worker`), comment `:83-85`
- Test: `tests/test_zen_fleet.py` (extend `test_worker_inherits_confirm_and_mode` area)

**Interfaces:**
- Consumes: `RuntimeOptions(root, resume, mode)`, `prepared.broker.set_confirm`, `FakeWorkerFactory.prepare` (records `self.modes[str(path)]`).
- Produces: a worker built with `mode="ask"` whenever the router's mode is `"zen"`; other modes (`ask`, `plan`) inherit unchanged.

Design authority: `2026-11-10-zen-mode-design.md` requires approvals to surface to the UI. The fleet's current comment claims intentional inheritance, but `permissions.py` `gate.mode=="zen"` bypasses confirm — so inheriting `zen` silently auto-approves worker mutations. Downgrade at the worker boundary only; the router keeps its own mode.

- [ ] **Step 1: Write the failing test:**

```python
async def test_zen_router_workers_run_in_ask_mode(env):
    env.mode = "zen"                      # router autonomy
    await env.fleet.spawn(...)            # use the real spawn entry the suite uses
    assert env.factory.modes[worker_path] == "ask"
    # and a plan router still inherits plan
```

(Mirror the existing `test_worker_inherits_confirm_and_mode` setup at `tests/test_zen_fleet.py:328`; assert both the zen→ask downgrade and that a non-zen mode is unchanged.)

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_zen_fleet.py -k worker -q`
Expected: FAIL — the zen worker records mode `"zen"`.

- [ ] **Step 3: Implement** in `_build_worker`: `mode = "ask" if self.mode == "zen" else self.mode`, pass to `RuntimeOptions`. Update the `:83-85` comment to state the boundary: workers never inherit the router's zen bypass; mutating tools surface as approvals.

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/test_zen_fleet.py -q`
Expected: PASS (existing inheritance test still green for ask/plan).

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/zen/fleet.py tests/test_zen_fleet.py
git commit -m "fix(zen): workers downgrade zen mode to ask so approvals surface"
```

---

### Task 8: `[zen] max_concurrent` config pass-through

**Files:**
- Modify: `zenagent/config.py` (`UserConfig` field + `[zen]` parse)
- Modify: `zenagent/cli/rpc/ops_zen.py:108-116` (pass `max_concurrent`)
- Modify: `docs/guide/zen-mode.md:64` (drop "预留")
- Test: `tests/test_config.py` (extend), `tests/test_zen_fleet.py` or an ops-level test

**Interfaces:**
- Consumes: `_section_table(data, section, target)`, `_positive_int(table, key, default, section, target)`, `ZenFleet(max_concurrent=...)`.
- Produces: `UserConfig.zen_max_concurrent: int` (default 3); `ops_zen._zen_enable` constructs `ZenFleet(max_concurrent=config.zen_max_concurrent, ...)`.

- [ ] **Step 1: Write the failing config test:**

```python
def test_zen_max_concurrent_parsed(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[zen]\nmax_concurrent = 5\n")
    conf = load_user_config(cfg)   # use the real loader the suite uses
    assert conf.zen_max_concurrent == 5

def test_zen_max_concurrent_default(tmp_path):
    conf = load_user_config(tmp_path / "absent.toml")
    assert conf.zen_max_concurrent == 3
```

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_config.py -k zen_max_concurrent -q`
Expected: FAIL — attribute missing.

- [ ] **Step 3: Implement.** Add `zen_max_concurrent: int = 3` to `UserConfig`. Parse a `[zen]` table via `_section_table` + `_positive_int(table, "max_concurrent", 3, "zen", target)`. `[zen]` is global-only (add to the `global_only` list so a project layer cannot raise its own concurrency). In `ops_zen._zen_enable`, pass `max_concurrent=<config>.zen_max_concurrent` to `ZenFleet`.

- [ ] **Step 4: Add the pass-through test** asserting `_zen_enable` builds the fleet with the configured cap (spy on `ZenFleet` construction or assert `fleet.max_concurrent`). Run RED then GREEN.

- [ ] **Step 5: Update the guide** `docs/guide/zen-mode.md:64`: remove "预留" — `[zen] max_concurrent` is now wired.

- [ ] **Step 6: Run to verify GREEN + commit**

Run: `uv run pytest tests/test_config.py tests/test_zen_fleet.py -q`
Expected: PASS.

```bash
git add zenagent/config.py zenagent/cli/rpc/ops_zen.py docs/guide/zen-mode.md tests/test_config.py
git commit -m "feat(zen): wire [zen] max_concurrent from config into the fleet"
```

---

### Task 9: Dev bridge fidelity — default→error, zen_fleet mock, swarm_notice

**Files:**
- Modify: `desktop/src/dev/bridge.ts:799-800` (default case), add `case 'zen_fleet'`
- Create: `desktop/src/dev/zenMock.ts` (`handleZenFleet(state, payload)`)
- Modify: `desktop/src/dev/swarmMock.ts` (emit `swarm_notice` on agent→you mention)
- Modify: `desktop/src/dev/bridge.ts:29` (`BridgeState extends ... ZenMockState`)
- Test: `desktop/tests/bridge.test.ts` (extend the T1 harness)

**Interfaces:**
- Consumes: `bootBridge` harness (T1), `SwarmMockState`, the swarm event sink, `RosterItem`.
- Produces: `handleZenFleet` returning `enable`/`disable`/`state`/`transcript` results mirroring `ops_zen.py`; an unknown request kind returns an **error** frame, not fake success; a posted agent message mentioning `you` emits a `swarm_notice` frame with `swarm:true`.

- [ ] **Step 1: Write the failing tests** in `desktop/tests/bridge.test.ts`:

```typescript
test('unknown request kind returns an error frame, not fake success', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'no_such_kind', id: 'x' })
  assert.equal(frame.type, 'error')
  assert.equal(frame.ok, undefined)
})

test('zen_fleet enable returns a router roster', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'zen_fleet', op: 'enable', id: 'z' })
  assert.equal(frame.ok, true)
  assert.equal(frame.enabled, true)
  assert.ok(Array.isArray(frame.roster) && frame.roster.some((r: any) => r.role === 'router'))
})

test('zen_fleet state reflects a prior enable', async () => {
  const { invoke } = bootBridge()
  await invoke({ type: 'zen_fleet', op: 'enable', id: 'z' })
  const frame = await invoke({ type: 'zen_fleet', op: 'state', id: 's' })
  assert.equal(frame.enabled, true)
})
```

- [ ] **Step 2: Run to verify RED**

Run: `cd desktop && node --test tests/bridge.test.ts`
Expected: FAIL — unknown kind returns `{ok:true}`; `zen_fleet` falls through to the fake-success default.

- [ ] **Step 3: Implement `zenMock.ts`.** `ZenMockState` carries `zenEnabled: boolean` + a fixed router/worker roster. `handleZenFleet` switches on `op`: `enable`→`{ok:true, enabled:true, roster, session}`, `disable`→`{ok:true, enabled:false, roster:[]}`, `state`→current, `transcript`→a small read-only row set; unknown op→error frame. Wire `case 'zen_fleet': return handleZenFleet(state, payload)` in bridge.ts and extend `BridgeState`.

- [ ] **Step 4: Change the default case** at `bridge.ts:799-800` to `return { type: 'error', id: payload.id, message: \`unknown request kind: ${String(payload.type)}\` }`.

- [ ] **Step 5: Emit `swarm_notice` in the mock.** In `swarmMock.ts`, when a posted message's author is an agent and its mentions include `you`, emit a `swarm_notice` frame (`swarm:true`, `agent`, `text`) through the same sink that emits `swarm_event`. Add a bridge/swarm test asserting the notice frame appears with `swarm:true`.

- [ ] **Step 6: Run to verify GREEN**

Run: `cd desktop && node --test tests/bridge.test.ts tests/swarmWiring.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add desktop/src/dev/bridge.ts desktop/src/dev/zenMock.ts desktop/src/dev/swarmMock.ts desktop/tests/bridge.test.ts
git commit -m "fix(desktop): dev bridge returns errors for unknown kinds and mocks zen_fleet"
```

---

### Task 10: Zen e2e — enter, roster, drawer, refusal, stop, paused-resume

**Files:**
- Create: `desktop/e2e/zen.spec.ts`
- Modify: `desktop/e2e/harness.ts` (only if a zen fixture helper is needed; reuse `initialState`)

**Interfaces:**
- Consumes: `boot`, `initialState` from `./harness`; the T9 `zen_fleet` mock; `data-zen-stop`, `data-worker-row`, the T6 paused banner.
- Produces: a Playwright spec covering the zen golden path and the stop/paused/resume edge.

- [ ] **Step 1: Write the spec** mirroring `desktop/e2e/swarm.spec.ts` structure:

```typescript
import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness'

test('entering zen shows the router roster and opens a worker drawer', async ({ page }) => {
  await boot(page, initialState())
  // switch workspace mode to zen via the ModeSwitcher
  await expect(page.locator('[data-worker-row]').first()).toBeVisible()
  // open a worker drawer, assert read-only rows render
})

test('stopping the fleet shows the paused banner; resume re-enables', async ({ page }) => {
  await boot(page, initialState())
  // enter zen, click [data-zen-stop]
  await expect(page.locator('[data-zen-paused]')).toBeVisible()
  // click resume → roster returns
})

test('a busy-slot refusal surfaces a notice and reverts the mode', async ({ page }) => {
  // drive the mock to refuse enable; assert the error notice + coding mode
})
```

(Use the real selectors present in `ZenRoster.tsx`/`ModeSwitcher.tsx`; add `data-zen-paused` in T6 if not already present. Pin the refusal path with a `BridgeState` flag the T9 mock honors, e.g. `zenBusy: true`.)

- [ ] **Step 2: Run to verify RED**

Run: `cd desktop && npx playwright test e2e/zen.spec.ts`
Expected: FAIL — selectors/flows missing before T6/T9 wiring is exercised end-to-end (some may already pass; the paused/resume and refusal cases must fail first).

- [ ] **Step 3: Make the spec pass** by completing any missing mock flag (`zenBusy`) or selector, without weakening assertions.

- [ ] **Step 4: Run to verify GREEN**

Run: `cd desktop && npx playwright test e2e/zen.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/e2e/zen.spec.ts desktop/e2e/harness.ts desktop/src/dev/zenMock.ts
git commit -m "test(desktop): zen e2e for roster, drawer, refusal and stop/resume"
```

---

## Batch ④ — Orchestration hardening

### Task 11: Rebuild serialization + delete race + pending_apply drain

**Files:**
- Create: `zenagent/app/swarm/frames.py` (`SwarmFrames` mixin — extract manager.py:764-798)
- Modify: `zenagent/app/swarm/manager.py` (`SwarmManager(SwarmFrames)`, remove the extracted helpers; `delete_role:313-364`, `_apply_role:550-569`, `_rebuild_agent:532-548`, `_execute:602-636`)
- Modify: `docs/arch-gov/manifest.json` (register `app/swarm/frames.py`)
- Test: `tests/test_swarm_manager.py` (extend)

**Interfaces:**
- Consumes: `SwarmAgent.pending_apply`, `self.runners`, `self.agents`, `_build_agent`, `_close_runtime`.
- Produces: `SwarmFrames` mixin (`_write`, `_emit_message`, `_emit_task`, `_emit_agent`, `_append_task`); a per-role `asyncio.Lock` serializing rebuild/apply/delete; `delete_role` that cancels+awaits the run, closes the **current** runtime, and pops the **current** agent atomically; `pending_apply` drained after a run without loss.

LOC budget: manager.py is at 800. **Step 1 extraction must land first** and free ~35 lines; the lock + race fixes then fit under the cap. Verify `wc -l` after each step.

- [ ] **Step 1: Extract `SwarmFrames` (refactor, no behavior change).** Move `_write`/`_emit_message`/`_emit_task`/`_emit_agent`/`_append_task` (manager.py:764-798) into `frames.py` as a mixin class referencing `self._write_frame`, `self.agents`, `self.store` and the module record builders (import `_message_record`/`_task_record` from `room.py`). Make `SwarmManager(SwarmFrames)`. Run the existing swarm suite to prove no behavior change:

Run: `uv run pytest tests/test_swarm_manager.py -q`
Expected: PASS. `wc -l zenagent/app/swarm/manager.py` < 800.

- [ ] **Step 2: Write the failing race tests:**

```python
async def test_delete_role_mid_run_leaks_no_runtime(manager, ...):
    # start a task, apply a config edit (pending_apply set), delete the role
    # assert: the run is cancelled/awaited, exactly one runtime closed,
    #         the agent removed, no orphan runtime left open,
    #         and NO swarm_agent frame broadcast for the deleted role after delete
    ...

async def test_pending_apply_survives_a_run(manager, ...):
    # queue an apply while a task runs; after the run ends the new config is built
    # assert: a swarm_agent frame with pendingApply:true was emitted, then the rebuild lands
    ...

async def test_concurrent_rebuilds_serialize(manager, ...):
    # fire two applies for one role concurrently; assert builds do not interleave
    # (the per-role lock holds across close+build)
    ...
```

(Use the suite's existing fake runtime/runner fixtures; assert on closed-runtime counts and emitted frames, not on mocks' internal calls.)

- [ ] **Step 3: Run to verify RED**

Run: `uv run pytest tests/test_swarm_manager.py -k "delete_role_mid_run or pending_apply_survives or concurrent_rebuilds" -q`
Expected: FAIL — A4 leak (old runtime closed, new agent popped), pending_apply loss, interleaved builds.

- [ ] **Step 4: Implement.** Add `self._role_locks: dict[str, asyncio.Lock]` (lazily per role). Wrap `_rebuild_agent`, the apply path, and `delete_role`'s critical section in `async with self._role_locks.setdefault(role.id, asyncio.Lock())`. In `delete_role`: under the lock, cancel the run, `await` it, close `self.agents[role.id].runtime` (the **current** one), then pop — never close an old runtime while popping a new agent. In `_execute`'s `finally`, drain `pending_apply` under the same lock so a concurrent apply is not lost. Emit `pendingApply:true` via `_emit_agent` when an apply is queued behind a run.

- [ ] **Step 5: Run to verify GREEN + budget**

Run: `uv run pytest tests/test_swarm_manager.py -q && python3 scripts/check_architecture.py`
Expected: PASS; manager.py ≤ 800.

- [ ] **Step 6: Register `frames.py` in the manifest** (`app.swarm` node file list) and commit:

```bash
git add zenagent/app/swarm/frames.py zenagent/app/swarm/manager.py docs/arch-gov/manifest.json tests/test_swarm_manager.py
git commit -m "fix(swarm): serialize per-role rebuilds, fix delete race and drain pending_apply"
```

---

### Task 12: Run watchdog (swarm + zen)

**Files:**
- Modify: `zenagent/app/swarm/runner.py:57-58` (bare `prompt` + `wait_for_idle`)
- Modify: `zenagent/app/zen/fleet.py:229` (`_run` bare prompt)
- Modify: `zenagent/config.py` (reuse `task_timeout_s` or add a zen/swarm watchdog knob — decide in step 1)
- Test: `tests/test_swarm_runner.py`, `tests/test_zen_fleet.py`

**Interfaces:**
- Consumes: `runner.abort_outcome`, `agent.abort()`, `asyncio.wait_for`, the existing `task_timeout_s` config.
- Produces: a wall-clock budget around each role/worker run; on timeout the run is aborted, the task marked `failed` with reason `timeout`, and the runtime reclaimed (no orphan).

- [ ] **Step 1: Decide the knob.** Prefer reusing `task_timeout_s` (already parsed, global-only) over a new mechanism (memory: 已有原语能承载就不加新机制). If the swarm/zen budget must differ from subagents, add `zen_run_timeout_s`/`swarm_run_timeout_s` mirroring `task_timeout_s`; otherwise reuse. Record the choice in the ledger. `0` disables the watchdog (preserve current behavior).

- [ ] **Step 2: Write the failing tests:**

```python
async def test_swarm_run_times_out_and_reclaims(runner, ...):
    # a prompt that never completes; timeout_s small
    # assert: abort_outcome == ("failed", "timeout"), agent.abort called, task failed,
    #         and the _sem slot is released so a queued _pending task can start
    ...

async def test_zen_worker_times_out(env):
    # worker prompt hangs; assert the fleet reclaims the slot and reports failure
    ...
```

- [ ] **Step 3: Run to verify RED**

Run: `uv run pytest tests/test_swarm_runner.py tests/test_zen_fleet.py -k timeout -q`
Expected: FAIL — the bare await hangs (use a short test timeout so RED is observable, not a real hang).

- [ ] **Step 4: Implement.** Wrap the run in `asyncio.wait_for(..., timeout)` when the knob > 0; on `TimeoutError` set `abort_outcome=("failed","timeout")`, call `agent.abort()`, and let the existing finally/reclaim path close the runtime. Keep `wait_for_idle()` inside the budget.

- [ ] **Step 5: Run to verify GREEN + commit**

Run: `uv run pytest tests/test_swarm_runner.py tests/test_zen_fleet.py -q`
Expected: PASS.

```bash
git add zenagent/app/swarm/runner.py zenagent/app/zen/fleet.py zenagent/config.py tests/test_swarm_runner.py tests/test_zen_fleet.py
git commit -m "feat(swarm,zen): wall-clock run watchdog with abort and reclaim"
```

---

### Task 13: Lifecycle hard stop — close fails approvals, cancel race, fleet closing recheck

**Files:**
- Modify: `zenagent/app/swarm/manager.py:128-168` (`close`, esp. `:167` `_approvals.clear()`)
- Modify: `zenagent/app/swarm/runner.py:51-52` (`run` resets `abort_outcome` first line)
- Modify: `zenagent/app/zen/fleet.py:211-252` (`_run` checks `_closing` before build, not after), `:295-315` (`_flush_reports` finally), `close:357-367`
- Test: `tests/test_swarm_manager.py`, `tests/test_swarm_runner.py`, `tests/test_zen_fleet.py`

**Interfaces:**
- Consumes: `self._approvals: dict[str, asyncio.Future]`, `APPROVAL_TIMEOUT_S`, `runner.abort_outcome`, `fleet._closing`, `fleet._wake_task`.
- Produces: `close()` fails every pending approval future promptly (no 120 s stall); a cancel issued before `run()` starts is not swallowed; a fleet worker built after `_closing` is set is torn down instead of started; a cancelled `_flush_reports` does not reschedule itself (no "Task exception was never retrieved").

- [ ] **Step 1: Write the failing tests:**

```python
async def test_close_fails_pending_approvals(manager):
    # register a pending approval future, call close()
    # assert: the future resolves to "deny" (or raises) promptly, not after APPROVAL_TIMEOUT_S
    ...

async def test_pre_run_cancel_is_not_swallowed(runner):
    runner.abort_outcome = ("cancelled", "aborted")   # cancel before run
    await runner.run(...)
    assert runner.abort_outcome == ("cancelled", "aborted")  # not reset to None

async def test_fleet_worker_built_after_close_is_torn_down(env):
    # set _closing during _build_worker; assert the worker is closed, not prompted
    ...

async def test_cancelled_flush_does_not_reschedule(env):
    # buffer a report, start _flush_reports, cancel its task while _closing is False
    # assert: no replacement _wake_task is created (no orphan task on a tearing-down loop)
    ...
```

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_swarm_manager.py tests/test_swarm_runner.py tests/test_zen_fleet.py -k "close_fails or pre_run_cancel or built_after_close or cancelled_flush" -q`
Expected: FAIL — futures cleared without resolution; `run()` resets `abort_outcome=None`; fleet starts a worker after closing; the cancelled flush reschedules itself.

- [ ] **Step 3: Implement.**
  - `manager.close`: before `self._approvals.clear()`, iterate pending futures and `fut.set_result("deny")` (or `set_exception`) if not done, so `_confirm_callback`'s `wait_for` returns immediately.
  - `runner.run`: do not blindly reset `abort_outcome=None` at :52; preserve a pre-existing cancel outcome (only reset when starting fresh, e.g. `if self.abort_outcome is None: ...` or check a cancel flag first).
  - `fleet._run`: recheck `self._closing` **after** `_build_worker` returns; if closing, close the prepared runtime and return without prompting.
  - `fleet._flush_reports` finally (`:311-315`): also guard against the current task being cancelled — `if self._report_buffer and not self._closing and not asyncio.current_task().cancelled():` before `create_task`, so a flush cancelled during teardown does not spawn an orphan task.

- [ ] **Step 4: Run to verify GREEN + commit**

Run: `uv run pytest tests/test_swarm_manager.py tests/test_swarm_runner.py tests/test_zen_fleet.py -q`
Expected: PASS.

```bash
git add zenagent/app/swarm/manager.py zenagent/app/swarm/runner.py zenagent/app/zen/fleet.py tests/test_swarm_manager.py tests/test_swarm_runner.py tests/test_zen_fleet.py
git commit -m "fix(swarm,zen): hard stop fails approvals, preserves pre-run cancel, rechecks closing"
```

---

### Task 14: Orchestration test coverage + flake fix

**Files:**
- Modify: `tests/test_zen_fleet.py:358` (`test_reports_within_the_batch_window_merge` flake → latch)
- Test: `tests/test_swarm_manager.py` (concurrency cap, deny branch), `tests/test_zen_fleet.py` (deny branch)

**Interfaces:**
- Consumes: the latch pattern already used by `test_concurrency_cap_queues:213` (`_await_runtime:230`), `fleet._wake_task`, the broker gate futures.
- Produces: a deterministic batch-window test; a swarm concurrency-cap test; an approval **deny** branch test for both swarm and zen.

- [ ] **Step 1: Write the failing/flaky-proof tests:**

```python
async def test_swarm_concurrency_cap_queues(manager):
    # max_concurrent=N; admit N+1 tasks; assert the (N+1)th waits until a slot frees
    # use a latch (yield until runner started), never sleep-vs-window timing
    ...

async def test_swarm_approval_deny_marks_task(manager):
    # broker raises an approval; respond "deny"; assert the tool is refused and the task outcome reflects it
    ...

async def test_zen_approval_deny(env):
    # worker approval denied; assert the mutation does not run
    ...
```

- [ ] **Step 2: Fix the flake** at `test_zen_fleet.py:358`: replace `sleep(0.02)`-vs-window-`0.05` timing with a latch — yield until `fleet._wake_task` is pending (the batch window armed), then release the second worker; assert the two reports merged into one router message. No wall-clock race.

- [ ] **Step 3: Run to verify RED** (new tests fail before any gap is closed; the flake fix is verified by repetition)

Run: `uv run pytest tests/test_swarm_manager.py tests/test_zen_fleet.py -k "concurrency_cap or approval_deny or batch_window" -q`
Expected: new coverage FAILs if a gap exists; the batch-window test PASSes deterministically.

- [ ] **Step 4: Close any real gap** the new tests expose (deny branch wiring, cap enforcement) with minimal code; keep tests green.

- [ ] **Step 5: Prove the flake is gone**

Run: `uv run pytest tests/test_zen_fleet.py::test_reports_within_the_batch_window_merge -q --count=20` (if `pytest-repeat` is available) else run the single test 20× in a shell loop.
Expected: 20/20 PASS, no timing flake.

- [ ] **Step 6: Full gate + commit**

Run: `bash scripts/check.sh`
Expected: PASS (ruff + architecture budget + full pytest).

```bash
git add tests/test_zen_fleet.py tests/test_swarm_manager.py
git commit -m "test(swarm,zen): cover concurrency cap and deny branch; deflake batch-window merge"
```

---

## Final whole-branch review

After Task 14, run the full gate once more (`bash scripts/check.sh`), then dispatch a fresh reviewer on the most capable model against `git merge-base main HEAD`..HEAD with this plan, the review report, and the ledger's `Ruling:` lines. Re-grade findings by user effect; fix Critical/Important in one TDD pass; ledger minors. Then use superpowers:finishing-a-development-branch (the integration decision is the user's).