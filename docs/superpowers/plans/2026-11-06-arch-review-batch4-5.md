# 批次 4/5：前端收敛与文档门禁（执行记录）

**Spec:** `docs/reviews/2026-11-06-architecture-review.md`（I10-I18、文档漂移表）

## 已落地

| 改动 | 文件 | 测试 |
|---|---|---|
| `RunStatus.to_wire()`；RPC `status` 附带完整快照（`status` 键），平铺键保留给老客户端 | `app/views.py`、`cli/rpc.py` | `tests/test_rpc_v2.py::test_status_carries_the_complete_run_status` |
| `build_broker_for(...)`：策略+门+broker 一处构造，三个前端不再各写一遍；**修掉只有 RPC 传 `read_only` 的分叉** | `app/permissions.py`、`cli/{main,repl,rpc}.py` | `tests/test_permissions.py`、`test_cli.py`、`test_repl_logic.py`、`test_rpc_v2.py` |
| `tests/test_docs_paths.py`：文档里被引用的 `zenagent/**.py` 必须存在（首次运行即抓到 `providers/retry.py`） | 新测试 | 自证 |
| 文档纠偏：`architecture.md` 增 `app/` 层与规则、`file-map.md` 增应用层表、`tools-internals.md` 重写 `task` 段与 `AgentTool` 字段、`contracts.md` §3/§4/§6、`testing.md` 去掉硬编码条数、`feature-map.md` 组件路径、`events.md` `turns` | `docs/**` | `test_docs_paths.py` 等 |

## 未落地（下一批，按价值排序）

1. **RPC 的 session 操作走 `CommandRegistry`**：`rpc.py::_session` 手写 new/fork/resume/undo/delete/
   compact/retry/export/promote 八个操作，与 `app/commands.py` 的同一动作返回两个契约。
   风险最高（desktop 直接消费这些 verb），需要先给 `CommandResult` 一个稳定 wire 形态。
2. **`RuntimeOptions.from_namespace(args)`**：`main.py`/`repl.py`/`rpc.py` 仍各自 22-25 个关键字段；
   共同点是 argparse dest 名，但 `main.py` 有 45 个 flag、`rpc.py` 有自己的 24 个解析器，
   需要先合并成共享 `add_runtime_arguments(parser)`，否则 `from_namespace` 只是把重复搬个地方。
3. **`agent/session.py` 的会话库拆分**（批次 3 Task 4 的延后项，见批次 3 ledger）。
4. **REPL 弃用 `render.AssistantLiveRenderer`**，改读 `chat.transcript`：会改动流式渲染，
   需要一轮视觉回归，属于独立分支的活。
