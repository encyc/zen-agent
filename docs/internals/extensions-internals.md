# 扩展内核：窄接口 + 隔离

## 设计目标

让用户「改 harness 不用改源码」，同时**不让扩展碰到循环内部**。所以接口只有四类贡献点，
没有 `agent` 内部对象、没有事件拦截、没有替换 loop 的能力。

## 加载流程（`extensions.py`）

```
load_extensions(paths)
  └─ for each path:
       spec = importlib.util.spec_from_file_location("zenagent_extension_<stem>_<i>", path)
       module = module_from_spec; sys.modules[name] = module
       exec_module(module)
       register = getattr(module, "register", None)   # 必须有
       register(ExtensionAPI(stem))
  └─ 合并：tools / commands / prompt_fragments / hooks
```

每个失败点都只记 warning 并跳过：

- 文件不存在；
- import 失败（SyntaxError/ImportError）；
- 没有 `register` 或不可调用；
- `register()` 自身抛异常。

`sys.modules` 里的半成品模块会被移除，避免污染后续 import。

## ExtensionAPI

```python
api.register_tool(tool)              # AgentTool，同样过权限门
api.register_command(name, description, handler)
api.add_system_prompt(text)          # 追加片段，自动 strip/去空
api.on(event_type, callback)         # 订阅 AgentEvent
```

合并规则：

- 命令重名：**先注册的赢**，后者 warning（内置斜杠命令又优先于所有扩展命令）。
- prompt 片段：按扩展顺序拼接，空行分隔。
- hooks：按 event_type 合并成列表。

## 接线点

- headless（`cli/main.py`）：工具并入 `tool_list`；prompt 片段并入 `extra_prompt`；
  hooks 在 Agent 创建后订阅，异常被捕获并打印。
- REPL（`cli/repl.py`）：额外把命令表交给 `handle_slash`（未知命令时先查扩展），
  补全表合并命令；`/reload` 重新发现（hooks 不重复订阅，避免副作用翻倍）。
- Agent 事件流是唯一的观察面：扩展不能阻止事件、不能改 run 结果。

## 安全边界

- 扩展是**本机 Python 代码**，拥有进程全部权限——不是沙箱。只加载你信任的文件。
- 扩展工具与内置工具走同一个 `PermissionGate`；`--read-only` 同样拒绝。
- 扩展 hook 抛异常只打印 warning，不影响 run。

## 写扩展的检查清单

- 工具有明确 schema，参数错误返回 `is_error` 结果而不是抛异常；
- 命令 handler 返回字符串（显示给用户）或 None；
- 不要在 hook 里做长时间阻塞操作（它在事件分发路径上）；
- 不要假设 hook 顺序（按扩展顺序，但别依赖）；
- 需要跨轮状态就自己存模块级变量。

## 源码对照

- `zenagent/extensions.py`（`ExtensionAPI`、`load_extensions`、`LoadedExtensions`）
- CLI 接线：`zenagent/cli/main.py`、`zenagent/cli/repl.py`（`discover_runtime`、`/reload`）
- 测试：`tests/test_extensions.py`
