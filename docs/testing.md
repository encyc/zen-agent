# 测试策略

## 三个闸门

| 闸门 | 命令 | 内容 | 何时跑 |
|---|---|---|---|
| 单元/集成测试 | `bash scripts/check.sh` | `uv sync` + `ruff check` + `pytest` | 每次改动（唯一 CI 闸门） |
| 打包发布 | `bash scripts/release-check.sh` | 构建 sdist/wheel、校验版本与包内容、两种安装各跑一遍 CLI | 打 tag 前（publish workflow 再跑一次） |
| 行为评测 | `./scripts/eval.sh` | 18 条真实模型用例 | 改 prompt/工具/接线后，按需 |

评测**不进** `check.sh`：它需要 key、会花钱、结果受模型随机性影响。

`.venv` 把创建时的绝对路径烧进了 `activate` 和控制台脚本的 shebang，**仓库目录改名/移动后
不能复用**：`pytest`/`zen` 会报 `bad interpreter` 或 `No module named zenagent`。
手动重建是 `rm -rf .venv && uv sync --extra cli --dev`；`check.sh` 也会检测 `activate` 里
记录的旧路径并自动重建，所以改名后直接跑闸门即可。

`release-check.sh` 也不进 `check.sh`：它要构建并由 wheel 安装，约 3 秒（缓存热时）。
默认用 `requires-python` 下限建临时 venv；`--skip-build` 复用刚构建的 `dist/`，
`--python 3.13` 换解释器。手动触发 publish workflow 只做 dry run，只有推 `v*`
tag 才会真的上传。

## 测试布局（约 697 条）

| 文件 | 覆盖 |
|---|---|
| `test_types.py` / `test_events.py` | 类型序列化、事件结构、AbortToken |
| `test_loop.py` | 循环、工具轮、终止条件、并行/串行、length 截断、错误隔离、steering |
| `test_agent_class.py` | 订阅、abort、wait_for_idle、reset、并发规则 |
| `test_validate.py` | schema 子集校验 |
| `test_registry.py` / `test_transport.py` | `safe_stream`、SSE 解析、重试、用量解析 |
| `test_openai_stream.py` / `test_openai_request.py` / `test_anthropic.py` / `test_openai_responses.py` / `test_google.py` | 四协议 fixture 回放 + 请求体断言 |
| `test_retry.py` | 可重试分类、默认透传（保流式）、重试缓冲、退避、abort 早停 |
| `test_streaming_behavior.py` | 流式回归：delta 必须在 run 结束前到达 ChatController |
| `test_repl_streaming.py` | REPL 每个 delta 必须 flush（不被 stdout 行缓冲吞掉） |
| `test_catalog.py` | 目录/定价/兼容开关 |
| `test_tools_fs.py` / `test_tools_bash.py` / `test_tools_search.py` | 工具行为与边界（二进制、截断、超时、进程组） |
| `test_permissions.py` | 权限优先级、危险命令、read-only |
| `test_session.py` / `test_compact.py` | JSONL、恢复、裁剪、压缩新文件 |
| `test_context_files.py` / `test_images.py` | AGENTS.md、图片大小/类型 |
| `test_mcp_client.py` / `test_mcp_manager.py` | 握手/工具/超时/崩溃、迟到重连、热替换 |
| `test_skills.py` / `test_templates.py` / `test_extensions.py` | 文件即扩展、隔离、命令 |
| `test_cli.py` / `test_repl_logic.py` / `test_completion.py` / `test_render.py` / `test_theme.py` / `test_doctor.py` | CLI/REPL 接线、渲染、主题 |
| `test_rpc.py` | JSONL 协议 |
| `test_export.py` / `test_usage.py` / `test_telemetry.py` / `test_crash.py` / `test_trace.py` | 可观测性 |
| `test_smoke.py` | 端到端冒烟（fake stream） |

原则：

- **单元测试不联网**：provider 测试全部用 `tests/fixtures/*.txt` 的 SSE 回放 +
  `httpx.MockTransport`；agent 测试用 fake `StreamFn`。
- **隔离用户环境**：autouse fixture 把 `load_user_config` 换成默认值、sessions 目录指向 tmp，
  避免读到你本机的 `~/.zenagent`。
- **断言行为而非实现**：尽量断言事件序列/结果/文件内容，不依赖内部函数调用次数。

## 写一个新测试的模板

```python
async def test_xxx():
    agent = Agent(model=..., system_prompt="s", api_key="k", stream_fn=fake_stream(...))
    events = []
    agent.subscribe(events.append)
    await agent.prompt("hi")
    assert agent.termination in (None, "stop")
    assert [e.type for e in events][-1] == "agent_end"
```

fake stream 的最小形态见 `examples/custom_tool.py`。

## 行为评测（`evals/`）

- 位置：`evals/cases/*.json`，格式见 `evals/README.md`。
- 运行：`./scripts/eval.sh --model deepseek-flash [--filter NAME] [--repeats 3]`。
- 每条 case 在独立临时目录运行，断言**结果**（文件内容、输出、工具名），不断言具体调用路径。
- 选项支持 S2 能力：`skills` / `extensions` / `mcp` / `tool_deny` / `read_only`。
- 断言：`tools`、`output_contains`、`files`、`files_absent`、`tool_errors_contain`、`termination`、`max_cost`。
- 回归对比：

```bash
./scripts/eval.sh --report base.json
./scripts/eval.sh --report new.json --baseline base.json   # 有 regression 时 exit 1
```

原则：**assert outcome, not route**。例如「修复 bug」不应断言必须用 `edit_file`——用 bash/sed 也可能正确。
只有需要固定路由的场景（比如测某个工具），才在 prompt 里明确要求工具名。

## 当前结果

- 单元/集成：697 passed（3.11 / 3.14；CI 跑 ubuntu + macos），ruff clean。
- 行为评测：18/18（3 repeats，$0.014，120k tokens）。

## 已知未覆盖

- prompt_toolkit 的真实终端交互（输入法、窗口缩放、补全下拉）只做了逻辑层测试；
- 真实 provider 的线上兼容性只靠评测和手工 trace，没有录制回放的全部组合；
- 长会话（>100 turns）的稳定性没有自动化测试；
- MCP 只对官方 filesystem server 做过真机验证，没有覆盖所有 server 变体。
