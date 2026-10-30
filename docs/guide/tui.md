# TUI 使用指南

`zen` 在真实终端里默认进入 Textual 全屏界面；`zen --no-tui` 回落到行式 REPL，
管道/非 TTY 环境自动回落。

## 布局

```
┌──────────────┬──────────────────────────────────────┐
│ SESSION      │  › user message                      │
│  <file>      │  assistant markdown                  │
│ MODEL        │  ▸ read_file {...} · ok · 12ms       │
│  deepseek…   │  done · in 2.1k / out 180 · $0.0003  │
│ RUNTIME      │                                      │
│  idle/run    │                                      │
├──────────────┴──────────────────────────────────────┤
│ deepseek-flash · thinking high · 2.1k in · $0.0003 │
├─────────────────────────────────────────────────────┤
│ > type a message…                                   │
└─────────────────────────────────────────────────────┘
```

- 左侧栏：`ctrl+b` 开关；窄于 100 列时自动隐藏。
- 底部状态栏：模型、thinking、tokens、花费、上下文占用、队列数。
- 对话区：工具调用默认折叠成一行，聚焦后 `enter`/`space` 展开；`edit_file` 的 diff 在展开后显示。

## 键位

| 键 | 行为 |
|---|---|
| `enter` | 发送；run 进行中则进入 follow-up 队列 |
| `shift+enter` | 输入换行 |
| `ctrl+s` | steer：把输入插入当前 run |
| `ctrl+c` | 中断当前 run（应用不退出） |
| `ctrl+x` | 清空 follow-up 队列 |
| `ctrl+b` | 开关侧边栏 |
| `ctrl+p` | 命令面板（模型/会话/帮助/退出） |
| `ctrl+q` | 退出，打印一行会话摘要 |

## 模型管理

`ctrl+p` → `model`（或 `/model`）打开选择器：

- `●` 标记当前模型；未配置 API key 的模型置灰并显示原因，不能选择。
- `enter` 切换，下一轮生效；上下文窗口、thinking 默认值、session id 同步更新。
- 编辑 provider/密钥请改 `~/.zenagent/config.toml`，TUI 不做内联编辑。

## 会话管理

`ctrl+p` → `sessions`（或 `/sessions`）打开选择器：

- `enter` 恢复会话（运行中会先中断当前 run 再切换）。
- 删除走「移到 `sessions/.trash/`」，不做不可逆 unlink；当前会话删除后自动新建空会话。
- fork/导出在 REPL 里仍可用（`/fork`、`/export`）；TUI v1 未提供对应按键。

## 排障

- 界面异常/无法启动：`TERM=dumb`、极老 conhost、非 TTY 环境会自动回落到 REPL。
- 想看纯文本输出：`zen --no-tui`。
- 只想跑一次：`zen -p "..."`（headless，不涉及 TUI）。
