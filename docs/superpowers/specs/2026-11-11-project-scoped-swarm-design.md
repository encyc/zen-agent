# Project-Scoped Swarm — Design Spec

**Date:** 2026-11-11
**Status:** Draft for review
**Branch (at execution):** a new worktree off `main`, separate from `fix/zen-swarm-review`
**Depends on:** the zen×swarm review-fix plan landing first (it pins the wire frame contract this feature extends).

## 1. Problem

Swarm is **global** today, unlike coding and zen which are per-project:

- One `SwarmManager` per RPC host (`RpcHost._swarm`, [host.py:68](../../../zenagent/cli/rpc/host.py)); one room at `~/.zenagent/swarm/` (`state_root()`, [roles.py:72](../../../zenagent/app/swarm/roles.py)); one global role library at `~/.zenagent/agents/` (`agents_root()`, [roles.py:64](../../../zenagent/app/swarm/roles.py)).
- Swarm frames carry `swarm:true` and **no project tag** — they are emitted through `RpcHost._send` ([host.py:566](../../../zenagent/cli/rpc/host.py)), which does not stamp `project`/`session` the way `RpcServer._send` does ([server.py:288-301](../../../zenagent/cli/rpc/server.py)). The desktop consumes them *before* the project filter ([appStore.ts:1075](../../../desktop/src/state/appStore.ts), [swarmWiring.ts:22](../../../desktop/src/state/swarmWiring.ts)).
- The UI states this explicitly: "The swarm room is global: it spans every project" ([messages-swarm.ts:180](../../../desktop/src/i18n/messages-swarm.ts)).

A user working in project A sees project B's swarm tasks and roles mixed into one room. Roles can optionally point at a project (`RoleManifest.root`, `inherit_project`), but the room and catalog themselves have no project identity.

## 2. Goal (agreed)

Make swarm a **per-project** surface like coding/zen:

1. **Per-project room** — each project owns its swarm room (tasks, messages, agents). Confirmed.
2. **Global role library + copy-to-project** — `~/.zenagent/agents/` stays a global template library; users copy a role into a project, where it becomes a project-local, independently editable config. Confirmed.
3. **Separate feature plan** — this is not a review fix; it ships on its own branch. Confirmed.

## 3. Design — reuse the existing project primitives

The codebase already separates project *inputs* (travel with the repo) from machine-local *runtime state* ([project.py:3-11](../../../zenagent/project.py)). The feature slots into that split rather than inventing a new one.

### 3.1 Where things live

| Thing | Kind | Location | Primitive reused |
|---|---|---|---|
| Project-local roles | input | `<root>/.zenagent/agents/<id>/` | the existing `<root>/.zenagent/` input layer (config/skills/MCP already live here) |
| Global role library | input | `~/.zenagent/agents/<id>/` (unchanged) | `agents_root()` |
| Room history (room.jsonl, tasks.jsonl) | state | `~/.zenagent/projects/<key>/swarm/` | `project_dir(root)` / `project_key(root)` |
| Role sessions | state | `~/.zenagent/projects/<key>/swarm/agents/<id>/sessions/` | `role_session_path`, re-rooted per project |
| Role workspace (cwd) | — | defaults to the **project root** for a project-local role | `RoleManifest.root` resolution |

`<key>` is `project_key(root)` (`<basename>-<sha256(root)[:8]>`), the same key the slot registry uses.

### 3.2 Per-project manager (backend)

`RpcHost._swarm` becomes a dict keyed by project key:

```python
self._swarm: dict[str, SwarmManager] = {}      # was: SwarmManager | None
```

- `_swarm_op` resolves the target project from `request.project` (falling back to the active slot's root), exactly like `_slot_for` ([host.py:237-263](../../../zenagent/cli/rpc/host.py)), then `_swarm_manager(root)` lazily builds/starts the manager for `project_key(root)`.
- `SwarmManager.__init__` gains a `root: Path` parameter; `SwarmStore` is rooted at `project_dir(root)/"swarm"`; `discover_roles(root)` reads the project-local agents dir; `role_session_path` is re-rooted per project.
- A project-local role's `root` defaults to the project root and `inherit_project` defaults true (it is *of* that project). The global library remains the source for copies.
- **Frame tagging:** the manager's `write_frame` callback stamps the project: `lambda p: self._send({**p, "project": str(root)})`. Swarm frames now carry `project` like `zen_fleet`; `swarm:true` stays (the desktop still consumes them ahead of the *session* filter, but routes by project).
- **Approval routing:** the `swarm:` request-id prefix gains the project key (`<project_key>:swarm:a<n>`), so `_resolve_swarm_prompt` finds the right project's manager after the user switches away — the same prefix-routed contract slots use ([host.py:149-153](../../../zenagent/cli/rpc/host.py), [server.py:344-348](../../../zenagent/cli/rpc/server.py)).
- **Lifecycle:** `RpcHost.close` closes every manager in the dict. A project's room outlives slot eviction (the manager owns its own role runtimes, independent of the interactive slots); `project close` tears down that project's manager. Idle-room resource cap is an open question (§7).

### 3.3 Role discovery + copy

- `discover_roles(root: Path | None)`: with a root, read `<root>/.zenagent/agents/`; the global `agents_root()` is read only as the **library** for the copy/browse surface, not auto-merged into a project's room.
- New `swarm` op `roles` action `copy` (or a `library` op): copies a global role's manifest + persona into `<root>/.zenagent/agents/<id>/`, then reloads the project room. Credentials stay `${KEY}` placeholders (never copied as secrets); the copy resolves credentials from the machine settings at build time, exactly as today.
- A project-local role is edited/deleted in place; the library original is untouched.

### 3.4 Desktop

- **Per-project room store:** `useSwarmStore` becomes keyed by project root (or follows the active project with a resync on switch, mirroring zen's roster resync at [appStore.ts:607](../../../desktop/src/state/appStore.ts)). The room shown is the active project's room.
- **Frame routing:** `applySwarmFrame` reads the frame's `project` tag; frames for the active project paint, background projects update their stored room (or drop + resync on switch, the zen pattern). The "consume before any project filter" logic relaxes to "consume before the *session* filter, route by project".
- **workspaceMode** stays a global setting ([stores.ts](../../../desktop/src/state/stores.ts)); entering swarm mode shows the *active project's* room. Switching project while in swarm mode swaps the room.
- **Copy affordance (entry at point of use):** the project's RoomPage roster panel ([RoomPage.tsx:336](../../../desktop/src/ui/pages/RoomPage.tsx) `data-swarm-new-role`) gains "copy from library", opening a picker of global roles; Settings → Swarm ([WorkspaceSettings.tsx](../../../desktop/src/ui/pages/WorkspaceSettings.tsx)) is the centralized second entry, reusing the same picker/editor. The existing role editor is reused, not reimplemented.
- **Copy updates:** the "room is global" settings string and the `inherit project context` hint are rewritten for the per-project model.

## 4. Migration

- Global `~/.zenagent/agents/` is **kept verbatim** as the library — no destructive change.
- The legacy global room `~/.zenagent/swarm/` is **archived** (left on disk, read-only). New rooms are per-project; the old global room is not auto-attributed to a project (it spanned all of them). A one-time notice can point the user at the archive. *Open question §7: archive vs migrate-to-active-project.*
- No wire `PROTOCOL_VERSION` bump is required if frames only *gain* an optional `project` tag and old clients ignore it; but the desktop routing change is breaking for a mixed old-client/new-server pair. *Open question §7: bump to 4 vs additive.*

## 5. Non-goals

- No change to zen mode or the coding session model.
- No cross-project rooms / no global "all projects" swarm view (the global room is retired, not kept alongside).
- No new credential storage; secrets stay in `~/.zenagent/settings.json`.
- No role-runtime kernel changes beyond re-rooting paths and discovery.

## 6. Invariants preserved

- `app/swarm` never imports `app/zen`; plugins call outward only; manifests carry `${KEY}` placeholders only.
- Role path containment (review-fix T2) applies to project-local roles too — a copied manifest still cannot read outside its role dir.
- The architecture LOC cap holds: `manager.py` is at 800; re-rooting must not grow it past the cap (extract path/discovery helpers if needed).

## 7. Open questions for review

1. **Idle-room resource cap.** Each project room owns resident role runtimes. With several projects open, that is several sets of runtimes. Cap concurrent live rooms (LRU teardown of idle rooms, mirroring `MAX_RESIDENT_SLOTS`), or keep them all resident until project close? Recommendation: an idle-room cap with teardown, reusing the slot-eviction idea.
2. **Legacy room.** Archive read-only (recommended, non-destructive) vs migrate the old global room into the currently-active project on first run?
3. **Protocol version.** Additive optional `project` tag (no bump, old clients ignore) vs bump `PROTOCOL_VERSION` to 4 to make the routing change explicit? Recommendation: additive tag + a contract test pinning that swarm frames carry `project`; bump only if mixed-version safety demands it.
4. **Per-project store vs resync.** Key the swarm store by root (all rooms live in memory) vs single active room + resync on switch (zen pattern, lighter)? Recommendation: single active room + resync, matching zen and the desktop's existing project-switch flow.

## 8. Task decomposition (sketch — expanded into the plan after spec approval)

1. Backend re-rooting: `SwarmManager(root)`, project-local `discover_roles`, `role_session_path`/`SwarmStore` per project; path helpers extracted to hold the LOC cap. Tests: two projects get isolated rooms/roles.
2. Host keying: `_swarm` dict, `_swarm_op` project resolution, frame `project` tagging, `swarm:` approval prefix with project key, lifecycle close. Tests: routing + background-project approval resolution.
3. Library + copy: `roles copy`/`library` op, credential-placeholder safety, project-local edit/delete. Tests: copy lands project-locally, library untouched, no secret leakage.
4. Desktop routing + store: project-tagged frame routing, per-project room (resync on switch), workspaceMode interaction. Tests: unit + wiring.
5. Desktop UI: RoomPage per-project room, "copy from library" picker (point of use + Settings reuse), copy strings rewrite. Tests: e2e (enter swarm in A vs B, copy a role, isolation).
6. Migration + contract: legacy archive notice, frame-contract test pinning `project` tag, arch-gov + changelog + guide updates.