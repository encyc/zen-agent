# TUI 使用指南

`zen` 在真实终端里默认进入 Textual 全屏界面；`zen --no-tui` 回落到行式 REPL，
管道/非 TTY 环境自动回落。

## 布局

```
┌────────────────────────────┬──────────────────────────────────────────────────┐
│ Session                    │  › 用 bash 运行 echo LOOK-OK                     │
│   20261030-122605.jsonl    │    ▸ bash {"command": "echo LOOK-OK"} · ok · 35ms│
│   4 messages               │    命令执行成功（exit code 0）。                 │
│                            │                                                  │
│ Model                      │  done · in 1762 / out 33 · cache 1664 · $0.0000  │
│   glm-5.3                  │                                                  │
│   context  914/1000000     │                                                  │
│   spend    $0.0000         │                                                  │
│                            │                                                  │
│ Runtime                    │                                                  │
│   idle                     │                                                  │
│   queued 0   steering 0    │                                                  │
│   8 models                 │                                                  │
│                            │                                                  │
│ ctrl+b  hide sidebar       │                                                  │
│ ctrl+p  commands           │                                                  │
├────────────────────────────┴──────────────────────────────────────────────────┤
│ glm-5.3   thinking high   1762 in / 33 out   $0.0000   context 0%             │
├───────────────────────────────────────────────────────────────────────────────┤
│ > type a message…                                                             │
└───────────────────────────────────────────────────────────────────────────────┘
```

- 左侧栏：`ctrl+b` 开关；窄于 100 列时自动隐藏。
- 底部状态栏：模型、thinking、tokens、花费、上下文占用、队列数。
- 对话区：用户消息用 `›` 起首；工具调用默认折叠成一行（`▸ name args · status · ms`），
  聚焦后 `enter`/`space` 展开结果与 diff。
- 配色：冷灰底 + 单一暖色强调（用户消息、焦点、运行中），其余信息靠字重与明度分层。

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
