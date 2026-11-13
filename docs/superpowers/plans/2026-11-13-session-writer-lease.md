# 编排底座 Phase 2：Session writer lease

> 日期：2026-11-13 · 分支：`feat/orchestration-foundation` · 基线：Phase 1 `bbe542b`
>
> 上游：[`2026-11-12-architecture-diagnosis.md`](../reviews/2026-11-12-architecture-diagnosis.md)
> Phase 1 计划：[`2026-11-12-orchestration-foundation.md`](2026-11-12-orchestration-foundation.md)

## 1. 要解决的问题

Phase 1 让“谁在跑”可查询，但没有阻止两个 Runtime 同时写同一个 session 文件。

当前结构上允许的路径：

- RPC host 可以开多个 slot；`_holder` 只覆盖部分路径，`session import`、
  `SessionManager` 的 `new/fork/resume` 仍可把另一个 slot 指到同一文件；
- 两个 `zen` 进程可以同时 `--resume` 同一 session；
- 两个 Runtime 同时 append，turn 会交错，`run_start` / `run_end` 也会错位。

Phase 2 的目标：**一个 session 文件同一时间只有一个 writer Runtime**。
router 槽位因此天然独占 Router session，不再依赖 UI 的“不要切过去”约定。

## 2. 范围

- 新增 `zenagent/app/session_lease.py`：
  - 进程内 registry（`path -> owner`，线程锁）；
  - 跨进程 advisory file lock（`~/.zenagent/locks/<sha256(path)>.lock`，
    POSIX `fcntl.flock` / Windows `msvcrt.locking`，拿不到即拒绝）；
  - `acquire_session(path, owner)` / `release_session(lease)` /
    `ensure_session_free(runtime, path)` / `bind_session_lease(runtime, path)`。
- `Runtime` 接线（最小改动）：
  - `session` setter 在换文件时 acquire 新租约、release 旧租约；
  - `switch_session` 先 `ensure_session_free`，避免先 abort 再失败；
  - `close` 释放租约。
- 冲突时抛 `SessionLeaseBusy`（不携带绝对路径，RPC 可安全回显）。
- 更新那些“旧 Runtime 还活着就 resume 同一文件”的测试：先 close，再 resume。
- 新增测试：同进程双 writer 拒绝、switch 到 busy 保持当前、close 后可 resume、
  同 owner 重绑 no-op、跨进程锁（subprocess）。

## 3. 非目标

- 不做读锁：只读 `Session` / `TranscriptBuilder` 不受影响。
- 不做跨机器的分布式锁；`~/.zenagent` 所在文件系统能提供 advisory lock 即可。
- 不改 RPC 协议；冲突走现有 failure/refusal 通道。
- 不做 Supervisor / mode 后端化（Phase 3/4）。

## 4. 设计

### 4.1 租约形状

```python
@dataclass(frozen=True)
class SessionLease:
    path: Path
    owner: object
    lock_fd: int | None = None
```

`owner` 是 Runtime 对象本身（进程内唯一）。registry 用 `threading.Lock`；
setter 在事件循环线程调用，锁内只做 dict + `flock`，不 await。

### 4.2 文件锁

```
~/.zenagent/locks/<sha256(resolved session path)>.lock
```

- POSIX：`fcntl.flock(fd, LOCK_EX | LOCK_NB)`。
- Windows：文件至少 1 字节，`msvcrt.locking(fd, LK_NBLCK, 1)`。
- 拿不到（`BlockingIOError` / `OSError`）→ `SessionLeaseBusy`。
- 锁文件不删除：删除会引入“unlink 后第三方重建”的经典竞态；文件很小。
- `zenagent_home()` 被测试隔离，锁文件不会写到真实 home。

### 4.3 Runtime 接线

```python
@session.setter
def session(self, session: Session) -> None:
    bind_session_lease(self, session.path)   # 失败则不改变当前 session
    self._session = session
    ...
```

- `bind_session_lease` 先 acquire 新租约，成功后才 release 旧租约；
  失败时旧租约与旧 session 都保持。
- 同一路径重绑（resume 同一文件）no-op。
- `switch_session(path)` 在 abort 之前 `ensure_session_free(self, path)`；
  目标 busy 时直接抛出，当前 run 不被打断。
- `close()` 末尾释放租约；未使用 session 的清理在释放前完成。

### 4.4 与 host/zen 的关系

- RPC host 的每个 slot 仍是一个 Runtime；host 不需要额外的 session 表。
- zen router 槽位是 Router session 的唯一 writer；从 coding 槽切到 router 时，
  `session resume` 走 host 的 `_holder`，不会创建第二个 writer。
- `session import` / `SessionManager` 的 switch 会走 Runtime setter，因此同样受保护。

## 5. 任务

### Task 1：租约模块

- [x] `tests/test_session_lease.py`：同路径双 acquire 拒绝、release 后可再 acquire、
  同 owner 重入 no-op、busy 异常信息不含路径。
- [x] 实现 `zenagent/app/session_lease.py`。
- [x] 跑目标测试。

### Task 2：Runtime 接线

- [x] `tests/test_session_lease.py` 增加：Runtime 双开拒绝、close 后 resume 成功、
  switch 到 busy 保持当前 session、同 owner 重绑 no-op。
- [x] `Runtime.session` setter / `switch_session` / `close` 接线。
- [x] 保持 `runtime.py` ≤ 1061 行 allowlist（等量删减）。

### Task 3：修既有测试

- [x] `tests/test_tui_subagent_resume.py`：resume 前 close 旧 runtime。
- [x] `tests/test_rpc_v2.py`：resume 前 close 旧 runtime。
- [x] 跑这两个文件。

### Task 4：跨进程锁

- [x] `tests/test_session_lease.py` 用 subprocess 验证第二个进程拿不到锁。
- [x] 确认 release 后第二个进程可以拿到。
- [x] Windows 分支至少做静态检查（本机 macOS 无法实跑）。

### Task 5：文档、门禁、提交

- [x] `docs/internals/persistence.md` 增加“writer lease”小节。
- [x] `docs/architecture.md` 模块表补 `session_lease`。
- [x] CHANGELOG Unreleased。
- [x] `ruff check .`、`scripts/check_architecture.py`、目标 pytest、全量 pytest。
- [x] 提交。

## 6. 验收

1. 同一个 session 已有一个 live Runtime 时，第二个 Runtime 的 bootstrap/switch 被拒绝；
2. 拒绝发生在 abort 之前，当前 session 和 run 不受影响；
3. 第一个 Runtime close 后，第二个可以正常 resume；
4. 另一个进程同时拿同一 session 的 lease 会被拒绝（subprocess 测试）；
5. `runtime.py` 不超过 1061 行；全量测试全绿。


## 7. 验收记录（2026-11-13 上午）

- 全量 Python：`2840 passed in 281.11s`（`.venv/bin/python -m pytest tests/ -q`）。
- Lint：`ruff check .` 全绿。
- 架构预算：`python scripts/check_architecture.py` → `architecture budget: ok`；
  `runtime.py` 1060 行（allowlist 1061）。
- 跨进程：`tests/test_session_lease.py::test_another_process_cannot_take_the_lock_async`
  通过（锁被占用时子进程 exit 17，release 后 exit 0）。
- 提交：`6341272 feat(session): add a single-writer lease`、
  `c25ce55 test(session): cover writer lease and resume handoff`、
  `6b4c77f fix(rpc): report a leased session as session_lease_busy`、
  本 docs 提交。
- 行为变化：resume 同一 session 前必须先 close 旧 Runtime；两个 CLI 进程也不能同时
  resume 同一文件。只读 transcript 不受影响。
