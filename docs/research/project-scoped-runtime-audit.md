# Project-scoped runtime: cwd audit and decoupling plan

Status: research complete, no production changes yet.
Branch: `refactor/project-scoped-runtime`.

## Executive summary

A `zenagent` process currently has exactly one project identity: `Path.cwd()`.
The desktop's stop/start-per-project model follows from that, and every visible
"reload" on a project switch is a consequence of replacing the process.

The audit found **45 direct `Path.cwd()` / `os.getcwd()` reads across 21
files**, but the number overstates the problem. Most lower-level helpers
already accept a root or cwd argument (`project.*`, `config.load_user_config`,
`mcp.config.load_mcp_config`, `permissions.build_broker_for`,
`skills.default_skill_roots`, `templates.default_template_roots`,
`workspace.workspace_files`, `git.git_status`). The real blockers are three:

1. **`Runtime` never stores a root.** It re-reads `Path.cwd()` in 11 places
   during construction and rediscovery.
2. **`RpcServer` is a single-runtime object.** One transport owns one
   `runtime`, one `chat`, one in-flight task, one approval table.
3. **Process-wide singletons assume one project.** `TaijiSupervisor` is the
   hard blocker (`_SINGLETON`, first call's arguments win); the task manager is
   shared; the job manager is already refcounted and safe.

A fourth, deeper coupling is easy to miss: **tools are module-level singletons
that resolve relative paths against the process cwd**. `bash` defaults to
`os.getcwd()` and `fs` tools open relative paths directly. Parameterizing
`Path.cwd()` alone does not make tools project-scoped.

Recommendation: do not build a process warm pool. Follow pi-desktop's shape —
one host process, per-project runtime slots, and a project pointer — in three
phases, with Phase A as a behavior-preserving refactor that proves isolation
inside a single process.

## Evidence map

### Already parameterized (no API change needed)

| Area | Entry point | Notes |
|---|---|---|
| Project identity | `zenagent/project.py:72` `find_project_root(cwd=None)` | Every path helper takes `cwd=` or `root=`. |
| User config | `zenagent/config.py:511` `load_user_config(cwd=...)` | Project layer already selectable. |
| MCP config | `zenagent/mcp/config.py:114` `load_mcp_config(path, cwd=...)` | Runtime just does not pass it. |
| Permissions | `zenagent/app/permissions.py:505` `build_broker_for(cwd=...)` | Policy already derived from a passed cwd. |
| Guard policy | `zenagent/app/safety.py:158` `GuardPolicy.from_config(cwd=...)` | `policy.workspace` holds the resolved root. |
| Skills / templates | `zenagent/skills.py:196`, `zenagent/templates.py:93` | Roots are arguments. |
| Workspace / git | `zenagent/app/workspace.py:171`, `zenagent/app/git.py:106` | Caches are keyed by path, so multi-project safe. |

### Direct cwd reads (45 across 21 files)

| Layer | Sites | Coupling |
|---|---|---|
| Runtime construction | `zenagent/app/runtime.py:327,358,397,403,530,597,610,667,800,866,875` | 11 reads; the root should be an instance field. |
| Session storage | `zenagent/agent/session.py:83,93,106,411,741` | `sessions_dir()` is implicit; the header records `os.getcwd()`. |
| Tool execution | `zenagent/agent/tool_exec.py:134,185`; `zenagent/tools/shell.py:145,151` | `ToolContext.cwd` is informational only; `bash` defaults to process cwd. |
| RPC file ops | `zenagent/cli/rpc/ops_files.py:47,49,59,71` | Per-request root needed. |
| RPC config ops | `zenagent/cli/rpc/ops_config.py:238,369,432,485,620,638` | Skills/MCP/config/project path per request. |
| RPC misc | `zenagent/cli/rpc/server.py:599` | `git_worktrees` uses process cwd. |
| Chat/status | `zenagent/app/chat.py:143,163` | Git status and reported cwd. |
| Commands | `zenagent/app/commands.py:764,792` | `dirs` and `handoff` write into cwd. |
| Prompt | `zenagent/app/support.py:45` | Compatibility wrapper; `PromptContext.cwd` exists. |
| Safety | `zenagent/app/safety.py:290` | Should use `policy.workspace`, not the process cwd. |
| Taiji | `zenagent/app/taiji/tools.py:227`, `zenagent/app/taiji/session.py:46` | Root and session header. |
| CLI-only | `zenagent/cli/prompts_cmd.py:53`, `zenagent/cli/doctor.py:169`, `zenagent/cli/completion.py:221`, `zenagent/crash.py:44` | Diagnostics/UX only; can keep a process-level default. |

### Process-wide state

| Singleton | Location | Verdict |
|---|---|---|
| `TaijiSupervisor` (`_SINGLETON`, first args win) | `zenagent/app/taiji/supervisor.py:539` | **Blocker.** Must be keyed by project root (or owned by a slot). |
| Task manager (`_task_manager`, one per loop) | `zenagent/agent/subagents_runtime.py:369` | Shared across slots; runs are id-keyed, but events need a project tag. |
| Job manager (refcounted) | `zenagent/tools/jobs.py:397` | Compatible: jobs carry their own cwd; refcount exists for exactly this. |
| Settings cache | `zenagent/settings.py:48` | User-global; safe. |
| `_RESERVED_PATHS` | `zenagent/agent/session.py:103` | Safe across projects; minor unbounded growth. |
| `_shared_async_client` | `zenagent/providers/transport.py:140` | Project-agnostic; safe. |
| Path-keyed caches | `zenagent/app/git.py:44`, `zenagent/app/workspace.py:55` | Keyed by absolute path; safe. |

### Rust and frontend

| Assumption | Evidence | Consequence |
|---|---|---|
| One child per shell | `desktop/src-tauri/src/rpc.rs:43` (`RpcState { proc: Option<Arc<Proc>> }`) | Multiplexing inside the child needs no Rust pool. |
| cwd is a spawn option | `desktop/src-tauri/src/rpc.rs:52-58`, `:391-394` | `--cwd` plus `current_dir`; one project per process. |
| Switch = restart | `desktop/src/state/appStore.ts:180,487`; `desktop/src/rpc/client.ts:86,140` | `rpc_stop` / `rpc_start` on every project change. |
| Event stream is unscoped | `serialize_event` payloads in `zenagent/cli/rpc/server.py:273` | Multiple slots need a project tag on events. |

## Why the tool layer is the hard part

`base_tools()` returns module-level `AgentTool` singletons
(`zenagent/tools/__init__.py:47`). `run_tool()` calls
`tool.execute(call.id, arguments, abort, forward)` with no context
(`zenagent/agent/tool_exec.py:56`), so:

- `bash` resolves its default cwd from `os.getcwd()`
  (`zenagent/tools/shell.py:142-153`);
- `read_file` / `write_file` / `edit_file` / `list_dir` / `glob` / `grep` open
  relative paths against the process cwd (`zenagent/tools/fs.py:77`).

Running two projects in one process therefore cannot rely on chdir (concurrent
turns would race). Relative-path resolution must come from an explicit
project root, either by:

- **A1. Per-runtime tool instances**: `base_tools(root)` builds copies whose
  closures carry the root; or
- **A2. Root on the execution path**: pass a project context through
  `run_tool` into every tools' `execute`.

A1 is closer to pi-desktop (per-turn `projectPath`) and avoids changing the
tool protocol; A2 centralizes resolution but touches every tool signature.
The rest of this plan assumes A1.

## Refactor plan

### Phase A — parameterize the root (no behavior change)

1. Add `root: Path` (resolved once via `find_project_root`) to `Runtime`;
   replace the 11 reads in `runtime.py` with `self.root`.
2. Thread the root into `sessions_dir`, `session_path`,
   `unique_session_path`, `latest_session`, and the session header.
3. Pass `cwd=self.root` into `load_mcp_config`, `build_broker_for`, and
   `system_prompt`.
4. Build tools per runtime with the root (A1): `bash`, `fs`, `glob`, `grep`,
   `list_dir`, plus `ToolContext.cwd`.
5. Make `_recursive_rm_reason` use `policy.workspace`.
6. Route `commands.dirs` / `handoff` and `chat.status` through `runtime.root`.
7. Route RPC `fs_*`, `memory`, `git_worktrees`, and config ops through the
   runtime root instead of `Path.cwd()`.
8. Keep `os.chdir` only as a CLI/TUI compatibility shim; it is no longer the
   source of runtime identity.

Acceptance: one process constructs two `Runtime`s with different roots and
proves that sessions, memory, skills, config, MCP, policy, prompt, and tool
cwd are isolated. The existing suite stays green; tests can migrate from
`monkeypatch.chdir` to explicit roots incrementally.

### Phase B — multiplex project slots in the RPC process

1. Split `RpcServer` into transport plus `RuntimeSlot`
   (`runtime`, `chat`, in-flight task, text buffer, approvals/asks, locks).
2. Add an optional `project` field to requests (root path). Missing field means
   the current slot, so v2 clients keep working. `PROTOCOL_VERSION` stays 2.
3. Tag every event frame with the slot's project; prefix approval/ask ids per
   slot so futures cannot collide.
4. Add a `project` request (`open` / `switch` / `close`). Opening builds the
   slot lazily: `Runtime` with the slot root, resume + transcript seed, then a
   background session list.
5. Cap resident slots (LRU, e.g. 4). Closing releases MCP, taiji ownership,
   and per-slot jobs.
6. Make taiji ownership per root (`get_taiji(root)`), and tag task-manager
   events with the project. The job manager stays global.

Acceptance: one child serves projects A and B; transcript, status, prompt,
cancel, and approvals route correctly; no transcript bytes cross directories;
A can keep running while B is opened, or the server refuses with a per-slot
`busy` frame.

### Phase C — frontend and shell

1. `client.ts` stops `rpc_stop` / `rpc_start` on project change and sends a
   `project` op instead. Restart remains for crashes and upgrades only.
2. `appStore.openProject` updates the active project and requests the target
   transcript; the existing resume-on-spawn path becomes the cold-start path.
3. Keep one `RpcState` child in Rust; add `rpc_restart` for recovery.
4. Add a transcript-pane LRU (2-3 projects) plus hover prefetch, matching
   pi-desktop's retained panes.

Acceptance: switching A -> B -> A spawns no process, paints the cached
transcript immediately, and revalidates in the background.

## Risks and open questions

- **Taiji.** Per-project lock (`own_taiji_lock`) and a process-wide supervisor
  conflict directly with slots. Decide whether taiji is single-project or
  per-root before Phase B.
- **MCP fan-out.** Each slot starts its own servers; lazily start on first
  prompt and dispose on slot eviction.
- **Memory.** Resident runtimes hold transcripts, tools, and MCP clients. Cap
  slots; do not follow pi-desktop's unbounded retention.
- **Busy semantics.** `busy` is per slot, not per process. The protocol and UI
  must say which project is busy.
- **Task/job events.** Subagent and job events need a project tag or they leak
  into the wrong pane.
- **Tests.** `monkeypatch.chdir` is used heavily; Phase A must give tests an
  explicit-root path so the suite does not depend on process state.

## Recommendation

Adopt the pi-desktop shape (one process, per-project slots, pointer switch)
rather than a warm process pool. The pool reduces switch time but keeps
duplicated state, duplicated MCP connections, and frame-tagging complexity,
and it does not remove the cwd coupling. Phase A is valuable on its own and is
the right first implementation step.
