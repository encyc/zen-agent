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

由应用写入，**不要手改**：`/settings` → `Model API keys…` / `Search API keys…` / `Search provider…`。
模型和 web 搜索共用这一个 store，设一次两边都生效。

```json
{
  "version": 1,
  "api_keys": {"DEEPSEEK_API_KEY": "…", "TAVILY_API_KEY": "…"},
  "prefs": {"search_provider": "deepseek"}
}
```

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

规则：

- 未知字段、类型错误都会**启动即报错**（不会静默忽略），错误信息带文件路径（项目层报项目文件路径）。
- 自定义 provider 覆盖同 id 的内置 provider 时给 warning。
- 密钥**不能**写进 config.toml，只能通过 `api_key_env` 指环境变量（值来自 env 或
  `~/.zenagent/settings.json`）。
- **项目层会进 git**：只放可共享配置，不要放密钥、账号、个人绝对路径。

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

1. 内置 catalog（`zenagent/catalog.yaml`）
2. `~/.zenagent/config.toml`（全局层）的 provider 覆盖 / 自定义 provider（覆盖内置会 warning）
3. `<项目根>/.zenagent/config.toml`（项目层）：标量/表覆盖，列表追加或并集（见上表）
4. CLI 参数（`-m`、`--thinking`、`--yes` 等）最高

## 源码对照

- 解析与校验：`zenagent/config.py::load_user_config`、`_merge_user_configs`、`apply_user_config`、`_parse_custom_providers`
- 目录路径：`config_path`、`zenagent/project.py::find_project_root`/`project_config_path`/`project_mcp_path`、
  `zenagent/agent/session.py::sessions_dir`、`zenagent/skills.py::default_skill_roots`、
  `zenagent/templates.py::default_template_roots`、`zenagent/mcp/config.py`、`zenagent/cli/keybindings.py::keybindings_path`
- 对应测试：`tests/test_config.py`、`tests/test_catalog.py`、`tests/test_project.py`、`tests/test_skills.py`、`tests/test_templates.py`
