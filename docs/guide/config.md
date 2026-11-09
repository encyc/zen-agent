# 配置与目录

## 目录布局

```
~/.zenagent/                     # 机器级（全局层，不进 git）
├── config.toml          # 模型/默认值/自定义 provider/扩展/工具策略（手写）
├── settings.json         # API key 与 UI 偏好（应用写，0600）
├── mcp.json             # MCP servers
├── keybindings.json     # 键位（可选）
├── skills/<name>/SKILL.md        # 用户级 skills
├── commands/<name>.md            # 用户级提示词模板
├── sessions/*.jsonl              # legacy 会话（只追加）
├── projects/<key>/sessions/      # 按项目隔离的会话
├── projects/<key>/backups/       # 按项目的写入快照（/restore 用）
├── logs/crash-*.log              # 崩溃日志
├── history                       # REPL 历史
└── metrics.jsonl                 # （--metrics 指定时）

<项目根>/.zenagent/              # 项目级（可提交，团队共享）
├── config.toml          # 全局 config.toml 的项目覆盖层（可选）
├── mcp.json             # 项目 MCP server 定义（可选）
├── skills/<name>/SKILL.md        # 项目级 skills
└── commands/<name>.md            # 项目级提示词模板
```

项目根 = 从 cwd 向上最近的含 `.git` 的目录（worktree 的 `.git` 文件也算；非 git 目录就是 cwd 本身）。
项目级 `{config.toml,mcp.json,skills,commands}` 优先于用户级（同名时项目赢，加载规则见下）。

## settings.json（API key 与偏好）

由应用写入，**不要手改**：`/settings` → `Model API keys…` / `Search API keys…` /
`Search provider…` / `Theme…` / `Language…`。模型和 web 搜索共用这一个 store，设一次两边都生效。

```json
{
  "version": 1,
  "api_keys": {"DEEPSEEK_API_KEY": "…", "TAVILY_API_KEY": "…"},
  "prefs": {"search_provider": "deepseek", "theme": "auto", "language": "auto"}
}
```

`prefs.theme` 是界面外观（`auto` / `dark` / `light`，见 [TUI § 外观与主题](tui.md#外观与主题)）；
优先级 `--theme` > `prefs.theme` > `auto`。`auto` 用终端 `COLORFGBG` 判断背景亮暗，
缺失或无法解析时回退 dark。

`prefs.language` 是界面语言（`auto` / `en` / `zh`，见 [界面语言](i18n.md)）；优先级
`--lang` > `prefs.language` > `ZENAGENT_LANG` > `LANG`/`LC_ALL`/`LC_MESSAGES` > en。
`auto` 跟系统 locale，`zh*` 归一成 `zh`。

- 权限 `0600`、目录 `0700`；写入是"临时文件 + rename"，断电不会留下半截文件；
- **设置里的 key 优先于环境变量**（应用里显式设的赢），env 仅作兜底（CI/headless）；
- 文件损坏不会被静默重置：`zen doctor` 的 `secrets` 行会报出来，key 仍从 env 兜底；
- 老版本误写在 `config.toml` 里的 `*_API_KEY="…"` 会在启动时自动迁进来（原文件不删）。

## config.toml（全局层 + 项目层）

默认读取两层：先 `~/.zenagent/config.toml`（机器级），再 `<项目根>/.zenagent/config.toml`
（项目级，可提交），合并后生效；任一层缺失就只用另一层，都缺失就是默认配置。显式传
`load_user_config(path=...)`（嵌入调用/测试）时只读该文件，不叠加项目层。

```toml
# ~/.zenagent/config.toml（全局层）
default_model = "deepseek-flash"

# 1) 只覆盖内置 provider 的地址/密钥来源
[providers.deepseek]
base_url = "https://mirror.example/v1"
api_key_env = "MY_DEEPSEEK_KEY"

# 2) 完全自定义 provider + 模型
[providers.custom.local]
name = "Local Gateway"
base_url = "http://127.0.0.1:8080/v1"
api_key_env = "LOCAL_API_KEY"
adapter = "openai-completions"      # 也支持 anthropic-messages / openai-responses / google-generative-ai

[[providers.custom.local.models]]
id = "my-model"
context_window = 32768
max_tokens = 4096
reasoning = false
supports_vision = true
cost = { input = 0.1, output = 0.2, cache_read = 0.01 }
compat = { thinking_format = "deepseek", supports_reasoning_effort = true }
default = true                       # 可选：该 provider 的默认模型

# 3) 扩展与工具策略
extensions = ["~/work/my-extension.py"]

[tools]
allow = ["read_*", "grep"]           # fnmatch：免确认
deny = ["write_file"]                # fnmatch：永远拒绝
```

项目层最小示例（`<项目根>/.zenagent/config.toml`，随仓库提交）：

```toml
default_model = "deepseek-flash"

[providers.deepseek]
base_url = "https://team-mirror.example/v1"   # 同 id 整条覆盖全局定义（内层不合并）
api_key_env = "TEAM_DEEPSEEK_KEY"             # 所以需要用的键要在这里写全

extensions = ["./tools/team_extension.py"]    # 相对本文件目录（.zenagent/）解析

[tools]
allow = ["apply_patch"]                       # 追加到全局 allow
deny = ["git_push"]                           # 追加到全局 deny；不能移除全局 deny
```

合并规则（全局在前，项目层在后）：

| 键 | 规则 |
| --- | --- |
| `default_model` | 项目层非空则覆盖全局 |
| `[providers.<id>]` | 按 provider id 合并；同 id 由项目层**整条**覆盖，其他 id 保留 |
| `[providers.custom.<id>]` | 同上（按 id 合并，同 id 项目层整条覆盖） |
| `extensions` | 追加：全局在前、项目在后，都保留；相对路径按**各自配置文件所在目录**解析 |
| `[tools] allow` | 追加并去重保序（全局 + 项目） |
| `[tools] deny` | 并集去重保序；项目层只能加严，**不能放宽**全局 deny |
| `[safety]` | **仅全局层**；项目层出现即报错（仓库不能给自己开权限） |
| `[backup]` | **仅全局层**；同上 |
| `[limits]` | **仅全局层**；同上（仓库不能放大所跑机器的提示词预算） |

规则：

- 未知字段、类型错误都会**启动即报错**（不会静默忽略），错误信息带文件路径（项目层报项目文件路径）。
- 自定义 provider 覆盖同 id 的内置 provider 时给 warning。
- 密钥**不能**写进 config.toml，只能通过 `api_key_env` 指环境变量（值来自 env 或
  `~/.zenagent/settings.json`）。
- **项目层会进 git**：只放可共享配置，不要放密钥、账号、个人绝对路径。

### [task]（仅全局层）

子代理（`task` 工具）的预算，默认值如下：

```toml
[task]
max_turns = 300                   # 子代理的模型轮次上限，超了就被截断
timeout_s = 900                   # 子代理的墙钟预算（秒），0 = 关闭看门狗
max_cost = 2.5                    # 子代理的花费上限（美元）；省略 = 不单独设限
keep_transcripts = 64             # 每个会话保留的 sidecar 转录条数
max_transcript_bytes = 33554432   # 保留转录的总字节数（32 MiB）
max_transcript_file_bytes = 8388608  # 单条转录超过 8 MiB 就截断并标记
```

- **仅全局层**：和 `[safety]` / `[backup]` 同规则，项目层出现 `[task]` 启动即报错——被检出的仓库
  不能给自己调大预算。
- `max_turns` 是**子代理**的轮次上限，与主循环的 `AgentConfig.max_turns`（`--max-turns`，内建兜底
  100 轮）是两套。超限不是错误：父模型收到的是
  `status="truncated" reason="max_turns" turns="N"`，看到的是一个被截断的结果。
- `timeout_s` 是墙钟保险丝：到点后子代理被温和中止（wind down），信封报
  `status="truncated" reason="timeout"`。默认 900 秒是防失控的宽松值，不是工作上限；`0` 关闭。
- `max_cost` 是单个子代理运行的花费上限，独立于会话级 `--max-cost`；设置了就**优先于**会话级值，
  省略则透传会话级值（也没有时即不设限）。超限的信封 `reason="max_cost"`。
- 各键都会在加载时校验：负数、布尔值、类型不符都会报错并指出 `task.<key>`；`timeout_s` 接受 0
  （关闭），其余必须是正数。
- 子代理的工具面跟随会话权限模式：zen 模式继承主 Agent 的激活工具集（黑名单除外），其他模式只拿
  只读工具（见[工具与权限](tools-permissions.md)）。

### [limits] 与参数页（仅全局层）

提示词 / 主循环的预算值，桌面设置「参数与预算」页读写的就是它们；手写等价：

```toml
[limits.skills]
max_index_bytes = 12288        # 系统提示里技能索引的总大小上限（超出显示 "more skills omitted"）
max_body_bytes = 32768         # 单次 read_skill / /skill: 注入正文的上限，超出截断并标注

[limits.context]
max_context_bytes = 32768      # AGENTS.md / CLAUDE.md 注入上下文的总大小上限
compact_threshold = 0.85       # 上下文用到窗口这一比例时触发自动压缩

[limits.agent]
max_turns = 100                # 主循环一次 run 的模型请求上限（--max-turns 覆盖它）
loop_guard_threshold = 5       # 连续完全相同的工具调用几次后注入"别再重复"提示
max_result_bytes = 40000       # 单个工具结果超过即只留头尾（全文可溢出到 artifact）

[limits.tools]
shell_max_output_bytes = 30000 # bash 输出保留量，超出只留头尾并标注
```

- **仅全局层**：项目层出现 `[limits]` 启动即报错——被检出的仓库不能放大它所跑机器的提示词预算。
- 每个键加载时校验范围，越界/类型错误启动即报错（不会静默 clamp）。
- 生效时机分两档，参数页会逐项标注：**立即**（技能索引/正文、loop 护栏、bash 输出，
  `apply_limits()` 热替换）与**新会话**（上下文注入、压缩阈值、`max_turns`、工具结果预算）。
- 「恢复默认」= 从文件里删掉这个键，上游改默认值时会跟着走。
- `--max-turns` / `--no-skills` / `--no-context-files` / `--no-auto-compact` 仍然只覆盖本会话，
  参数页会以"本会话被 … 覆盖"如实标注。

### [safety] 与 [backup]（仅全局层）

```toml
[safety]
allow_outside = ["~/scratch", "/tmp/work"]  # 允许写 workspace 之外的这些路径
allow_destructive = false                   # 放行破坏性 shell 命令
allow_protected = false                     # 放行受保护凭据路径

[backup]
enabled = true
max_file_bytes = 2097152                    # 单文件超过 2 MiB 不存内容
keep_runs = 50                              # 每个项目保留最近 50 次 run 的快照
```

- 这两个表的值会放宽安全边界，所以**只认全局层**：项目层（会进 git）出现
  `[safety]` / `[backup]` 时启动即报错，并提示把配置移到 `~/.zenagent/config.toml`。
- `allow_outside` 的相对路径按配置文件所在目录解析，`~` 展开；目录条目覆盖其下所有路径。
- CLI 的 `--allow-outside PATH` / `--allow-destructive` / `--allow-protected` 与配置取并集
  （flag 只能加、不能减）。
- `[backup] enabled = false` 时文件工具不再写快照，`/restore` 会提示不可用。

## mcp.json（全局层 + 项目层）

```json
{
  "servers": {
    "files": {"command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
    "remote": {"url": "https://example.com/mcp", "headers": {"authorization": "Bearer ..."}}
  }
}
```

同样读两层：`~/.zenagent/mcp.json`（全局）→ `<项目根>/.zenagent/mcp.json`（项目）。
按 server 名合并，**同名 server 项目层整条覆盖**，不同名的全局 server 保留；两层都缺失
即没有任何 server。CLI 显式传 `--mcp-file PATH` 时只读该文件，不叠加项目层。

项目层要进 git：只放 command/url/非敏感 env，**不要放 token**；需要凭据的 server
把 header/密钥放全局 `~/.zenagent/mcp.json`（不提交），或让 server 进程从环境变量读取。
详见 [MCP](mcp.md)。

## keybindings.json

```json
{"newline": "escape,enter", "submit": "enter", "abort": "c-c"}
```

只暴露安全可覆盖的三个动作；文件坏了回退默认值并 warning。

## 优先级与加载顺序

1. 内置 catalog（`zenagent/catalog.yaml`）——17 个 provider，完整清单见
   [providers.md](../internals/providers.md#内置-provider-清单17-个)
2. `~/.zenagent/config.toml`（全局层）的 provider 覆盖 / 自定义 provider（覆盖内置会 warning）
3. `<项目根>/.zenagent/config.toml`（项目层）：标量/表覆盖，列表追加或并集（见上表）
4. CLI 参数（`-m`、`--thinking`、`--yes`、`--allow-outside` 等）最高

## 源码对照

- 解析与校验：`zenagent/config.py::load_user_config`、`_merge_user_configs`、`apply_user_config`、`_parse_custom_providers`
- 目录路径：`config_path`、`zenagent/project.py::find_project_root`/`project_config_path`/`project_mcp_path`/
  `project_backups_dir`、
  `zenagent/agent/session.py::sessions_dir`、`zenagent/skills.py::default_skill_roots`、
  `zenagent/templates.py::default_template_roots`、`zenagent/mcp/config.py`、`zenagent/cli/keybindings.py::keybindings_path`
- 安全相关解析：`zenagent/config.py`（`[safety]`/`[backup]` 与项目层限制）、
  `zenagent/app/journal.py::load_backup_policy`
- 对应测试：`tests/test_config.py`、`tests/test_catalog.py`、`tests/test_project.py`、`tests/test_journal.py`、
  `tests/test_skills.py`、`tests/test_templates.py`
