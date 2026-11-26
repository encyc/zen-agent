# 自动化任务（automations）

到点自动在**一个固定的 session** 里跑一轮 agent，跑完的结论就追加在那个 session 里。
每一条 automation 属于一个项目，由那个项目自己的常驻 `zen daemon` 驱动。

```
zen automations add --name daily-report --daily 09:00 --prompt "跑取数脚本，读它的输出，找异常"
zen automations list
zen automations show daily-report
zen automations run daily-report          # 让 daemon 现在就跑一轮
zen automations disable daily-report      # 暂停（定义与历史都留着）
zen automations enable daily-report
zen automations rm daily-report           # 删定义；session 文件保留
```

`zen automations` 的所有子命令都作用在**当前目录所属项目**上，并且要求你在项目根执行。
`add` 不需要 API key，也不会创建 session 文件——会话是懒创建的，在它第一次真正跑起来时出现。

## 一条 automation 有五个字段

| 字段 | 含义 |
| --- | --- |
| 指令（`--prompt`） | 让 agent 干什么。原样进入提示词，不被改写。 |
| 工作目录 | 就是项目根。一个 daemon 只服务一个项目，跨项目的任务请在那个项目里另建一条。 |
| 时间表 | `daily HH:MM` 或 `every N[m\|h]`，本地时间。 |
| 超时（`--timeout`） | wall-clock 秒数，默认 1800。到点直接 `abort()` 那一轮。 |
| session | 第一次 `add` 时铸出的文件名，之后不变。你随时可以打开它继续聊。 |

## 三个文件

都在 `~/.zenagent/projects/<项目 key>/` 下，和账本、会话做邻居：

| 文件 | 谁写 | 谁读 | 说明 |
| --- | --- | --- | --- |
| `automations.json` | CLI / 桌面端 | daemon（按 mtime 热加载） | 定义。原子替换，改完不用重启 daemon。 |
| `automations.state.json` | **只有 daemon** | CLI / 桌面端 | 调度视角：上次何时跑、跳过没有、错过几轮。 |
| `automations.request.json` | CLI / 桌面端 | **只有 daemon**（消费即删） | "现在就跑"的信箱。 |
| `runs.jsonl` | 运行时 | CLI / 桌面端 | 每一轮的成本、时长、终止原因（`kind="automation"`）。 |

## 常驻：一项目一 daemon

```
zen daemon                 # 前台跑这个项目的调度器
zen daemon status          # 在跑吗、几条任务、下次何时醒
zen daemon install         # macOS launchd / Linux systemd --user，登录自起
zen daemon uninstall
```

- **没有 automation 的项目不装、不起。** `automations.json` 不存在就是这个意思。
- 一条 daemon 只服务一个项目。要跑第二个项目就在那个项目里再 `install` 一次，
  服务名按项目 key 区分，互不覆盖。
- 同一项目第二个 daemon 会拿不到锁并直接退出。
- daemon 挂了由 launchd 的 `KeepAlive` / systemd 的 `Restart=on-failure` 拉起来。
  升级 zenagent 之后 `zen daemon status` 会提示单元里记录的版本与当前不一致，重跑
  `zen daemon install` 即可。

## 权限：无人值守时它有多大的手脚

automation 以 `auto` 模式运行：**没有人会在旁边点"允许"**。真实边界是：

- 工作目录内外都能写，包括凭证路径（`~/.ssh`、`~/.zenagent`）；
- 破坏性 shell 命令（`rm -rf`、git 历史重写之类）照跑；
- **仍然被拒绝**：需要 `--allow-dangerous` 的灾难性命令；命中你全局配置
  `[tools] deny` 的任何工具；以及**读取**受保护的凭证路径
  （内核在 `auto` 下也会拦这一类读）。

也就是说：它能改你的文件，但读不到你的密钥。如果你要更紧的边界，目前的手段是
在全局 `~/.zenagent/config.toml` 的 `[tools] deny` 里列出来。

## 错过与跳过

- **错过一轮就补跑。** daemon 启动、机器从睡眠唤醒、或者某一轮跑得比周期还久时，
  该跑的那一轮会在下一次 tick 补上。
- **错过很多轮只补最近一轮。** `missed` 记录错过了几轮（`zen automations list` 可见）。
- **你正开着那个 session 时，那一轮会被跳过**，不做抢占、不排队堆积；下一个周期再试。
  这是为了让你随时能打开那个 session 说话，而不被 daemon 锁住。
- 刚 `add` 的任务不会立刻跑：没有历史时，下一次到期时间从现在算起。
- 跑完那一轮之后 daemon 立刻释放 session 的写租约，所以你随时可以 `zen --resume` 进去。

## 桌面端

右侧工作面板里的「自动化」页：按项目分组列出所有 automation，可以新建、暂停/恢复、
删除、以及"现在跑一次"。列表每 2 秒刷新。要看某一轮的细节，从侧栏打开这个 automation 的 session（panel 目前不提供跳转）。
