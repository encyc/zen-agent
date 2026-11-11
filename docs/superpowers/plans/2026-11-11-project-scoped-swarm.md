# Project-Scoped Swarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make swarm a per-project surface like coding/zen: each project owns its room (tasks, messages, agents), the global `~/.zenagent/agents/` stays a template library users copy into a project, and swarm frames route by project.

**Architecture:** Reuse the existing project primitives (`project.project_dir` / `project_key`, the `<root>/.zenagent/` input layer) rather than inventing new ones. `RpcHost._swarm` becomes a per-project-key dict of `SwarmManager`s; each manager is rooted at `project_dir(root)/"swarm"`, discovers project-local roles under `<root>/.zenagent/agents/`, and stamps `project` on every frame it emits. The desktop keeps a single active room and resyncs on project switch (the zen pattern), with frames filtered by project before they reach the swarm store.

**Tech Stack:** Python 3.15 (asyncio, no third-party deps beyond httpx), React 19 + TypeScript (node:test + Playwright) desktop, JSONL room logs.

**Spec:** `docs/superpowers/specs/2026-11-11-project-scoped-swarm-design.md`

## Decisions (resolved open questions — spec §7)

1. **Idle-room cap → idle-only LRU eviction.** `RpcHost` keeps `_swarm` as an `OrderedDict[key, SwarmManager]` capped at `max(1, self._max_slots)` (the same resident budget as interactive slots). On insert, the least-recently-used room with **no running task** is closed and dropped. A room with a running task or a pending approval is never evicted (its run would die); overshoot is allowed in that case. Reuse the existing OrderedDict LRU primitive; do not invent a new one.
2. **Legacy room → archive read-only.** `~/.zenagent/swarm/` is left on disk untouched; new rooms are per-project. No auto-migration/attribution. When a project room starts and the legacy `room.jsonl` exists, the manager appends one warning pointing at the archive.
3. **Protocol → additive `project` tag, no `PROTOCOL_VERSION` bump.** Swarm frames gain an optional `project: string`; the desktop filters by it. A backend contract test pins that every swarm frame carries `project`. (Same shape as `ZenFleetFrame.project?`.)
4. **Desktop → single active room + resync on switch.** One `useSwarmStore` holds the active project's room; a project switch or swarm-mode re-entry resets + reloads it (mirrors `syncZenState`). Frame routing moves from "consume before the project filter" to "consume after the project filter, before the fleet handler" so only the active project's frames reach the store.

## Global Constraints

- `app/swarm` never imports `app/zen`; plugins call outward only; manifests carry `${KEY}` placeholders only (never resolved secrets).
- Role path containment (role prompt/skills stay inside the role dir) applies to project-local roles too.
- `manager.py` stays under the 800-line architecture cap; put path/copy helpers in `roles.py`/`roster.py`, keying + eviction in `ops_swarm.py`/`host.py`.
- The single CI gate is `bash scripts/check.sh` (sync → ruff → architecture budget → pytest). Desktop tests are `cd desktop && node --test tests/*.test.ts`; e2e is `cd desktop && npx playwright test`.
- Tests use real code paths; mocks mirror the wire contract, never replace the behavior under test.

## Review Focus

Input classes / failure modes most likely to bite a user, each pinned by a test in the owning task:

1. **Two projects open → rooms isolated.** Messages/tasks/roles created in A never appear in B; switching resyncs. (Task 1 backend, Task 4/5 desktop)
2. **Background-project approval after switching away.** An approval whose request-id names project A must resolve project A's manager even when the active slot is now B. (Task 2)
3. **Copy leaks a secret or clobbers an edited role.** A copied library role keeps `${KEY}` placeholders (no resolved secret on disk) and refuses to overwrite an existing project role; library original is untouched. (Task 3)
4. **Legacy room not silently lost or mis-attributed.** `~/.zenagent/swarm/` is left intact; the user is told it is archived. (Task 6)
5. **Eviction kills live work.** The idle cap must never close a room with a running task or pending approval. (Task 2)

---

### Task 1: Backend re-rooting — per-project store, roles and sessions

**Files:**
- Modify: `zenagent/app/swarm/roles.py`
- Modify: `zenagent/app/swarm/roster.py`
- Modify: `zenagent/app/swarm/manager.py`
- Test: `tests/test_swarm_roles.py`, `tests/test_swarm_manager.py`

**Interfaces:**
- Produces:
  - `project_state_root(root: Path) -> Path` — `project.project_dir(root) / "swarm"`.
  - `state_root() -> Path` — **unchanged**, legacy `~/.zenagent/swarm` (archive detection only).
  - `agents_root() -> Path` — **unchanged**, global library `~/.zenagent/agents`.
  - `project_agents_root(root: Path) -> Path` — `Path(root) / ".zenagent" / "agents"`.
  - `discover_roles(root: Path | None = None) -> tuple[list[RoleManifest], list[str]]` — `root=None` reads the global library (`agents_root()`, today's defaults); `root=<path>` reads `project_agents_root(root)` and parses each role with `project_root=root`.
  - `parse_role(role_dir: Path, *, project_root: Path | None = None) -> tuple[RoleManifest | None, list[str]]` — when `project_root` is set and the manifest omits `root`, default `root=project_root`; when it omits `inheritProject`, default `True`; else today's defaults (`workspaces_root()/<id>`, `False`).
  - `role_session_path(role_id: str, *, root: Path, fresh: bool = False) -> Path` — under `project_state_root(root)/"agents"/<id>/sessions`.
  - `write_role(role_id: str, manifest: dict, *, root: Path) -> RoleManifest` — writes `<root>/.zenagent/agents/<id>/`.
  - `remove_role_dir(role_id: str, *, root: Path) -> None`.
  - `SwarmManager(*, root: Path, write_frame=..., stream_fn_factory=..., event_sink=..., max_concurrent=3)` — `self.root = root`; `self.store = SwarmStore(project_state_root(root))`; `start()`/`reload()` call `discover_roles(self.root)`; `_build_agent` calls `role_session_path(role.id, root=self.root, fresh=...)`; `update_role` calls `write_role(role_id, manifest, root=self.root)`; `delete_role` calls `remove_role_dir(role_id, root=self.root)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_swarm_roles.py`

```python
def test_project_state_root_is_under_the_project_dir(tmp_path, home):
    from zenagent.app.swarm.roles import project_state_root
    from zenagent import project
    root = tmp_path / "proj"
    root.mkdir()
    assert project_state_root(root) == project.project_dir(root) / "swarm"

def test_discover_roles_reads_the_project_local_agents_dir(tmp_path, home):
    root = tmp_path / "proj"
    (root / ".zenagent" / "agents" / "research").mkdir(parents=True)
    (root / ".zenagent" / "agents" / "research" / "manifest.json").write_text(
        json.dumps({"id": "research", "name": "R"}), encoding="utf-8"
    )
    roles, warnings = discover_roles(root)
    assert [r.id for r in roles] == ["research"]
    # project-local default: workspace is the project root, inherit_project on
    assert roles[0].root == root.resolve()
    assert roles[0].inherit_project is True

def test_library_role_keeps_isolated_defaults(tmp_path, home):
    # root=None still reads the global library with today's defaults
    base = agents_root()
    (base / "coder").mkdir(parents=True)
    (base / "coder" / "manifest.json").write_text(
        json.dumps({"id": "coder", "name": "C"}), encoding="utf-8"
    )
    roles, _ = discover_roles(None)
    assert [r.id for r in roles] == ["coder"]
    assert roles[0].inherit_project is False

def test_role_session_path_is_per_project(tmp_path, home):
    r1, r2 = tmp_path / "a", tmp_path / "b"
    r1.mkdir(); r2.mkdir()
    p1 = role_session_path("research", root=r1)
    p2 = role_session_path("research", root=r2)
    assert p1.parent != p2.parent
    assert "swarm" in p1.parts and "agents" in p1.parts
```

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/test_swarm_roles.py -q -k "project or library or session_path"`
Expected: FAIL — `project_state_root`/`project_agents_root` not defined; `discover_roles`/`parse_role`/`role_session_path` lack the new params.

- [ ] **Step 3: Implement in `roles.py`**

Add `project_agents_root`, `project_state_root`; thread `root` through `discover_roles`/`parse_role`/`role_session_path`. Import `from zenagent import project` (already imported). `parse_role` root/inherit defaults:

```python
explicit_root = raw.get("root")
if explicit_root:
    root = Path(str(explicit_root)).expanduser().resolve()
elif project_root is not None:
    root = Path(project_root).resolve()
else:
    root = (workspaces_root() / role_id).resolve()
...
inherit = raw.get("inheritProject")
if inherit is None:
    inherit = project_root is not None
role = RoleManifest(..., inherit_project=bool(inherit), ...)
```

Add a dedicated test asserting project-local path containment still holds (a project role's prompt escaping its role dir is rejected) — copy the existing containment test shape but drive it through `parse_role(role_dir, project_root=root)`.

- [ ] **Step 4: Update `roster.py` signatures**

`write_role(role_id, manifest, *, root)` writes under `project_agents_root(root)`; `remove_role_dir(role_id, *, root)` guards the same. Keep `agents_root()` import only for any legacy use (none after this).

- [ ] **Step 5: Update `manager.py`** — add required `root: Path` kwarg; replace `SwarmStore(state_root())` with `SwarmStore(project_state_root(root))`; `discover_roles()` → `discover_roles(self.root)`; `role_session_path(role.id)` → `role_session_path(role.id, root=self.root, ...)`; `write_role(role_id, manifest)` → `write_role(role_id, manifest, root=self.root)`; `remove_role_dir(role_id)` → `remove_role_dir(role_id, root=self.root)`. Drop the now-unused `state_root` import.

- [ ] **Step 6: Update `tests/test_swarm_manager.py` / `tests/test_swarm_roles.py` constructors** — every `SwarmManager(...)` passes `root=<a tmp project root>`; helper fixtures (`_write_role`, `env`) write roles under `<root>/.zenagent/agents/`. Update the existing role tests to pass `root=`.

- [ ] **Step 7: Add the isolation test** in `tests/test_swarm_manager.py`:

```python
async def test_two_projects_get_isolated_rooms(tmp_path, home):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, role in ((a, "alpha"), (b, "beta")):
        (root / ".zenagent" / "agents" / role).mkdir(parents=True)
        (root / ".zenagent" / "agents" / role / "manifest.json").write_text(
            json.dumps({"id": role, "name": role}), encoding="utf-8"
        )
    ma, mb = SwarmManager(root=a), SwarmManager(root=b)
    await ma.start(); await mb.start()
    try:
        assert set(ma.role_ids) == {"alpha"}
        assert set(mb.role_ids) == {"beta"}
        post = await ma.store.append_message  # type: ignore[truthy]
    finally:
        await ma.close(); await mb.close()
```

- [ ] **Step 8: Run GREEN**

Run: `uv run pytest tests/test_swarm_roles.py tests/test_swarm_manager.py -q`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add zenagent/app/swarm/roles.py zenagent/app/swarm/roster.py zenagent/app/swarm/manager.py tests/test_swarm_roles.py tests/test_swarm_manager.py
git commit -m "feat(swarm): root the manager, roles and sessions per project"
```

---

### Task 2: Host keying — per-project managers, frame tagging, approval routing, idle cap

**Files:**
- Modify: `zenagent/cli/rpc/ops_swarm.py`
- Modify: `zenagent/cli/rpc/host.py`
- Test: `tests/test_swarm_rpc.py` (new or existing swarm rpc test), `tests/test_swarm_manager.py` (for `has_active`)

**Interfaces:**
- Produces:
  - `SwarmManager.has_active() -> bool` — `bool(self._running)` (a pending approval is a running task, so it counts).
  - `RpcHost._swarm: OrderedDict[str, SwarmManager]` (was `SwarmManager | None`).
  - `_swarm_manager(root: Path) -> SwarmManager` — lazily builds keyed by `project_key(root)`, stamps `project` on frames, enforces the cap.
  - Approval/ask request-id prefix `f"{key}:swarm:a{n}"`; `_is_swarm_request_id(rid)` and `_swarm_key_from_request_id(rid) -> str | None`.

- [ ] **Step 1: Write failing tests** in `tests/test_swarm_rpc.py` (build a real `RpcHost` with a fake write sink; drive two projects):

```python
async def test_swarm_op_routes_by_project(host_factory, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_role(a, "alpha"); _seed_role(b, "beta")
    host = host_factory()
    await host._swarm_op({"type": "swarm", "op": "list", "project": str(a)}, "1")
    await host._swarm_op({"type": "swarm", "op": "list", "project": str(b)}, "2")
    assert [m.role_ids for m in host._swarm.values()] == [{"alpha"}, {"beta"}] or \
        {frozenset(m.role_ids) for m in host._swarm.values()} == {frozenset({"alpha"}), frozenset({"beta"})}

async def test_swarm_frames_carry_the_project_tag(host_factory, tmp_path):
    root = tmp_path / "p"; _seed_role(root, "alpha")
    host = host_factory()
    await host._swarm_op({"type": "swarm", "op": "post", "project": str(root), "text": "hi @alpha"}, "1")
    frames = [f for f in host.sent if isinstance(f, dict) and f.get("swarm")]
    assert frames and all(f.get("project") == str(root) for f in frames)

async def test_background_project_approval_resolves_after_switch(host_factory, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_role(a, "alpha"); _seed_role(b, "beta")
    host = host_factory()
    ma = await host._swarm_manager(a)
    rid = f"{project_key(a)}:swarm:a1"
    fut = ma._approvals.setdefault(rid, __import__("asyncio").get_running_loop().create_future())
    # active slot is elsewhere; the prefixed id must still reach project A
    assert host._resolve_swarm_prompt({"request_id": rid, "id": "x", "decision": "allow"}, "approval") is True
    assert fut.result() == "allow"

async def test_idle_room_cap_evicts_only_idle_rooms(host_factory, tmp_path):
    host = host_factory(max_slots=1)
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_role(a, "alpha"); _seed_role(b, "beta")
    ma = await host._swarm_manager(a)
    # simulate a running task in A so it must NOT be evicted
    ma._running["alpha"] = object()  # sentinel; has_active True
    await host._swarm_manager(b)
    assert project_key(a) in host._swarm  # active room survived
```

- [ ] **Step 2: Run RED** — `uv run pytest tests/test_swarm_rpc.py -q -k "project or tag or approval or cap"` → FAIL (`_swarm` is not a dict, no prefix).

- [ ] **Step 3: Add `has_active`** to `SwarmManager` (one line + docstring).

- [ ] **Step 4: Implement `ops_swarm.py`**

```python
async def _swarm_manager(self, root) -> SwarmManager:
    key = project_key(root)
    manager = self._swarm.get(key)
    if manager is None:
        manager = SwarmManager(
            root=root,
            write_frame=lambda p, r=root: self._send({**p, "project": str(r)}),
            event_sink=lambda rid, tid, ev, r=root: self._send_role_event(rid, tid, ev, r),
        )
        self._swarm[key] = manager
    else:
        self._swarm.move_to_end(key)
    if not manager._started:  # via a public started flag or has_been_started()
        await manager.start()
    await self._evict_idle_rooms()
    return manager

def _swarm_root(self, request: dict) -> Path:
    project = request.get("project")
    if isinstance(project, str) and project.strip():
        return find_project_root(project)
    if self._active is not None:
        return Path(self._active.server.project)
    raise Refusal("no project: open one first", code="no_project")
```

`_swarm_op` resolves `root = self._swarm_root(request)` once and passes it to every branch (`_swarm_roles` too). `_send_role_event(role_id, task_id, event, root)` adds `"project": str(root)`.

`_evict_idle_rooms`:

```python
async def _evict_idle_rooms(self) -> None:
    cap = max(1, self._max_slots)
    while len(self._swarm) > cap:
        victim = next((k for k, m in self._swarm.items() if not m.has_active()), None)
        if victim is None:
            break  # every resident room is busy; tolerate overshoot
        manager = self._swarm.pop(victim)
        await manager.close()
```

- [ ] **Step 5: Implement request-id routing** in `ops_swarm.py`

```python
def _swarm_key_from_request_id(rid: str) -> str | None:
    key, sep, _ = rid.partition(":swarm:")
    return key if sep else None

def _is_swarm_request_id(rid: str) -> bool:
    return ":swarm:" in rid or rid.startswith("swarm:")
```

`_resolve_swarm_prompt`: `key = _swarm_key_from_request_id(str(request_id)) or (active key)`; `manager = self._swarm.get(key)`; if None → the existing "no pending" error frame. Resolve on that manager.

- [ ] **Step 6: Update `host.py`** — `self._swarm: OrderedDict[str, "SwarmManager"] = OrderedDict()`, drop `_swarm_started`; the `_handle_line` detector uses `_is_swarm_request_id(request_id)`; `close()` iterates `self._swarm.values()` closing each then clears; `_swarm_manager` no longer returns a single object. Make the approval request-id minting (in `manager`/broker) use `f"{key}:swarm:a{n}"` — pass the key into the manager at build (`SwarmManager(root=..., approval_prefix=f"{key}:swarm:")`) or let the manager derive it from `project_key(self.root)`.

- [ ] **Step 7: Run GREEN** — `uv run pytest tests/test_swarm_rpc.py tests/test_swarm_manager.py -q` → PASS.

- [ ] **Step 8: Commit** `feat(swarm): key managers by project, tag frames, route approvals`

---

### Task 3: Library browse + copy-to-project

**Files:**
- Modify: `zenagent/app/swarm/roster.py`
- Modify: `zenagent/cli/rpc/ops_swarm.py`
- Test: `tests/test_swarm_roles.py` (copy), `tests/test_swarm_rpc.py` (ops)

**Interfaces:**
- Produces:
  - `copy_role(role_id: str, *, root: Path) -> RoleManifest` — copies `agents_root()/<id>` (manifest.json, prompt.md and any files it references inside the role dir) into `project_agents_root(root)/<id>`; raises `ValueError("role {id} already exists in this project")` if the target exists; never resolves `${KEY}` (bytes copied verbatim).
  - swarm `roles` op actions `library` (returns `[role_view(r) for r in discover_roles(None)[0]]`) and `copy` (`roleId`), both reload the project room after `copy`.

- [ ] **Step 1: Failing tests**

```python
def test_copy_role_lands_project_local_and_leaves_library(tmp_path, home):
    lib = agents_root() / "research"
    lib.mkdir(parents=True)
    (lib / "manifest.json").write_text(
        json.dumps({"id": "research", "name": "R", "mcpServers": {"x": {"env": {"K": "${MY_KEY}"}}}}),
        encoding="utf-8",
    )
    (lib / "prompt.md").write_text("be nice", encoding="utf-8")
    root = tmp_path / "proj"; root.mkdir()
    role = copy_role("research", root=root)
    copied = root / ".zenagent" / "agents" / "research"
    assert copied.is_dir()
    assert (copied / "prompt.md").read_text() == "be nice"
    assert (lib / "manifest.json").exists()  # library untouched
    # credential placeholder preserved verbatim on disk
    assert "${MY_KEY}" in (copied / "manifest.json").read_text()

def test_copy_refuses_to_clobber(tmp_path, home):
    (agents_root() / "r").mkdir(parents=True)
    (agents_root() / "r" / "manifest.json").write_text(json.dumps({"id": "r"}), encoding="utf-8")
    root = tmp_path / "proj"; (root / ".zenagent" / "agents" / "r").mkdir(parents=True)
    with pytest.raises(ValueError):
        copy_role("r", root=root)
```

Ops test: `roles action=library` returns the global role list; `action=copy` then `list` shows the copied role in the project room.

- [ ] **Step 2: RED** — `uv run pytest tests/test_swarm_roles.py -q -k copy` → FAIL (no `copy_role`).

- [ ] **Step 3: Implement `copy_role`** in `roster.py` using `shutil.copytree(agents_root()/role_id, project_agents_root(root)/role_id)` with a pre-existence guard; validate the id with `_ID_RE`; reparse the copy with `parse_role(copied, project_root=root)` and return it.

- [ ] **Step 4: Implement ops actions** in `_swarm_roles` (in `ops_swarm.py`): `library` → global list; `copy` → `copy_role(role_id, root=root)` then `await manager.reload()`; return `{"roles": manager.roles(), "warnings": ...}`.

- [ ] **Step 5: GREEN** — `uv run pytest tests/test_swarm_roles.py tests/test_swarm_rpc.py -q` → PASS.

- [ ] **Step 6: Commit** `feat(swarm): browse the role library and copy a role into a project`

---

### Task 4: Desktop frame routing + room resync

**Files:**
- Modify: `desktop/src/rpc/types-swarm.ts`
- Modify: `desktop/src/state/appStore.ts`
- Modify: `desktop/src/state/swarmStore.ts`
- Modify: `desktop/src/state/swarmWiring.ts`
- Test: `desktop/tests/swarmWiring.test.ts`, `desktop/tests/swarmStore.test.ts`, `desktop/tests/frames.test.ts`

**Interfaces:**
- Produces:
  - `Swarm*Frame` gains `project?: string` (5 frame interfaces).
  - `useSwarmStore.reset()` — clears messages/tasks/agents/roles/runRows/warnings, `loaded=false`.
  - `AppState.syncSwarmState()` → `syncSwarmState(get)`: no-op unless `workspaceMode === 'swarm'`; else `reset()` then `load()` (load sends `{type:'swarm', op:'list'}`, which the client stamps with the active `project`).
  - `handleFrame` consumes swarm frames **after** `frameBelongsTo` (project filter), before `applyFleetFrame`.

- [ ] **Step 1: Failing tests** (`desktop/tests/swarmWiring.test.ts`)

```ts
test('a tagged swarm frame for a background project is not applied', () => {
  reset(); setActiveProject('/p/a')
  const applied = applySwarmFrameFor(project('/p/b'), messageFrame('m1'))
  assert.equal(applied, false)
  assert.equal(useSwarmStore.getState().messages.length, 0)
})

test('syncSwarmState resets and reloads the active room', async () => {
  // seed a stale message, then sync → load replaces with the new project's room
})
```

And a `frames.test.ts` addition pinning that a `swarm:true` frame tagged with a non-active project is filtered out (returns false from `frameBelongsTo`).

- [ ] **Step 2: RED** — `cd desktop && node --test tests/swarmWiring.test.ts tests/frames.test.ts` → FAIL.

- [ ] **Step 3: Add `project?: string`** to the 5 frame interfaces in `types-swarm.ts`.

- [ ] **Step 4: Add `reset()`** to `swarmStore.ts`; keep `applyFrame` as-is (it only sees active frames now).

- [ ] **Step 5: Move swarm consumption** in `appStore.handleFrame`: delete the pre-filter `if (applySwarmFrame(frame)) return`; insert `if (applySwarmFrame(frame)) return` immediately after the `if (!frameBelongsTo(frame, handling.get().project)) return` guard (next to `applyFleetFrame`). Verify notices now only fire for the active project (update the wiring header comment).

- [ ] **Step 6: Add `syncSwarmState`** (in `appStore.ts` or a `swarmActions.ts`), wire `openProject` (after `syncZenState`) and `switchWorkspaceMode`/`zenActions.switchMode` on entering `'swarm'`, and a boot path if persisted mode is swarm.

- [ ] **Step 7: GREEN** — `cd desktop && node --test tests/swarmWiring.test.ts tests/swarmStore.test.ts tests/frames.test.ts` → PASS.

- [ ] **Step 8: Commit** `feat(desktop): route swarm frames by project and resync the active room`

---

### Task 5: Desktop UI — copy-from-library picker (point of use + Settings)

**Files:**
- Create: `desktop/src/ui/pages/LibraryPicker.tsx`
- Modify: `desktop/src/ui/pages/RoomPage.tsx`
- Modify: `desktop/src/ui/pages/WorkspaceSettings.tsx`
- Modify: `desktop/src/i18n/messages-swarm.ts`
- Modify: `desktop/src/state/swarmStore.ts` (add `copyRole`/`libraryRoles`)
- Test: `desktop/tests/swarmStore.test.ts`, `desktop/e2e/swarm.spec.ts`

**Interfaces:**
- Produces:
  - `useSwarmStore.libraryRoles(): Promise<SwarmRole[]>` → `{op:'roles', action:'library'}`.
  - `useSwarmStore.copyRole(id: string): Promise<{ok:boolean; message?:string}>` → `{op:'roles', action:'copy', roleId:id}` then `load()`.
  - `LibraryPicker({ onClose, onCopied })` — modal listing library roles with a "copy" button per row (disabled for ids already in the project room); `data-library-picker`, `data-library-copy={id}`.
  - `RoomPage` roster: a `data-swarm-copy-role` button beside `data-swarm-new-role`, opening `LibraryPicker`.
  - `WorkspaceSettings.SwarmSettings`: a `data-settings-copy-role` button reusing `LibraryPicker`.

- [ ] **Step 1: Failing store test** — `copyRole('research')` sends `{type:'swarm', op:'roles', action:'copy', roleId:'research'}` then reloads; `libraryRoles()` returns the library list.
- [ ] **Step 2: RED** — `cd desktop && node --test tests/swarmStore.test.ts` → FAIL.
- [ ] **Step 3: Implement store methods + `LibraryPicker` + wire RoomPage + SwarmSettings.**
- [ ] **Step 4: Rewrite strings** in `messages-swarm.ts` (zh + en): `swarm.settings.desc` → per-project ("每个项目拥有自己的 Swarm 房间…"), and `swarm.roleInheritProjectHint` → note project-local roles default to inheriting. Add `swarm.copyFromLibrary`, `swarm.libraryEmpty`, `swarm.copyExists` keys (zh + en).
- [ ] **Step 5: e2e** `desktop/e2e/swarm.spec.ts`:
  - `enter swarm in project A, copy a library role, and it appears only in A` — boot with two projects, enter swarm, copy `research`, assert `[data-swarm-agent="research"]`; switch to B, assert it is absent.
  - `copy from library is reachable from Settings → Swarm` — the second entry opens the same picker.
- [ ] **Step 6: GREEN** — `cd desktop && node --test tests/swarmStore.test.ts && npx playwright test e2e/swarm.spec.ts` → PASS.
- [ ] **Step 7: Commit** `feat(desktop): copy a library role into the project swarm room`

---

### Task 6: Migration + frame contract + docs

**Files:**
- Modify: `zenagent/app/swarm/manager.py` (legacy archive warning)
- Modify: `CHANGELOG.md`
- Modify: `docs/guide/zen-mode.md` (or a new swarm section)
- Modify: `tests/test_swarm_manager.py`, `tests/test_rpc_contract.py` (frame contract)
- Test: as above

**Interfaces:**
- Produces: on `SwarmManager.start()`, if `state_root()/"room.jsonl"` exists and `self.root`'s room has no prior messages, append `warnings.append("legacy global room archived at ~/.zenagent/swarm/; new rooms are per project")` (idempotent, one entry).

- [ ] **Step 1: Failing test** — a manager started for a project while the legacy `state_root()/room.jsonl` exists gets the archive warning; a manager with an existing project room does not re-warn.
- [ ] **Step 2: RED** → FAIL.
- [ ] **Step 3: Implement** the archive warning in `start()`.
- [ ] **Step 4: Frame contract test** in `tests/test_rpc_contract.py`: every swarm frame emitted (message/task/agent/notice/event) carries a `project` key equal to the manager root (drive a post through a host and assert).
- [ ] **Step 5: Docs** — CHANGELOG: a new "Unreleased → Changed" entry "Swarm is now per-project…"; guide: document per-project rooms, the global library + copy, and where the legacy room went (archive). Correct the old "room is global" text.
- [ ] **Step 6: Architecture check** — `python3 scripts/check_architecture.py` → ok; confirm no `app/` module is unmapped and `manager.py` ≤ 800 (extract a helper if it grew). Bump `docs/arch-gov/manifest.json` `version` only if the registration convention requires it.
- [ ] **Step 7: Full gate** — `bash scripts/check.sh` → all green.
- [ ] **Step 8: Commit** `docs(swarm): per-project rooms, archive notice, frame contract`

---

## Self-Review

**Spec coverage:** §3.1 locations → Task 1 (`project_state_root`, project-local discovery, per-project sessions). §3.2 per-project manager → Task 2 (dict, resolution, frame tag, approval prefix, lifecycle + idle cap). §3.3 discovery + copy → Tasks 1/3. §3.4 desktop → Tasks 4/5. §4 migration → Task 6 + Decision 2. §7 open questions → the "Decisions" section.

**Type consistency:** `project_state_root`/`project_agents_root`/`discover_roles(root)`/`parse_role(project_root=)`/`role_session_path(root=)`/`write_role(root=)`/`remove_role_dir(root=)`/`copy_role(root=)`/`has_active()` are used identically across tasks. `Swarm*Frame.project?`, `syncSwarmState`, `reset()`, `libraryRoles()`, `copyRole()` are named the same in the interfaces that produce and consume them.

**Review Focus:** each of the 5 lines has a named test in its owning task (Task 1 isolation, Task 2 approval-after-switch + cap-recursion, Task 3 copy safety, Task 6 legacy archive; Task 4/5 isolation e2e).

**Open risk for the reviewer:** the idle cap (Decision 1) and the "notices are now per active project" behavior change (Task 4) are the two places a reasonable user could be surprised; both are called out above and pinned by tests.
