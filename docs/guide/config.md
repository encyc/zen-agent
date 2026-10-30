# 配置与目录

## 目录布局

```
~/.zenagent/
├── config.toml          # 模型/默认值/自定义 provider/扩展/工具策略（手写）
├── settings.json         # API key 与 UI 偏好（应用写，0600）
├── mcp.json             # MCP servers
├── keybindings.json     # 键位（可选）
├── skills/<name>/SKILL.md        # 用户级 skills
├── commands/<name>.md            # 用户级提示词模板
├── sessions/*.jsonl              # 会话（只追加）
├── logs/crash-*.log              # 崩溃日志
├── history                       # REPL 历史
└── metrics.jsonl                 # （--metrics 指定时）
```

项目级覆盖：`<repo>/.zenagent/{skills,commands}/`，项目优先于用户级（同名时项目赢）。

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

## config.toml

```toml
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

规则：

- 未知字段、类型错误都会**启动即报错**（不会静默忽略），错误信息带文件路径。
- 自定义 provider 覆盖同 id 的内置 provider 时给 warning。
- 密钥**不能**写进 config.toml，只能通过 `api_key_env` 指环境变量。
- `extensions` 相对路径基于 config.toml 所在目录解析。

## mcp.json

```json
{
  "servers": {
    "files": {"command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
    "remote": {"url": "https://example.com/mcp", "headers": {"authorization": "Bearer ..."}}
  }
}
```

stdio 与 HTTP 二选一；`env` 可选。详见 [MCP](mcp.md)。

## keybindings.json

```json
{"newline": "escape,enter", "submit": "enter", "abort": "c-c"}
```

只暴露安全可覆盖的三个动作；文件坏了回退默认值并 warning。

## 优先级与加载顺序

1. 内置 catalog（`zenagent/catalog.yaml`）
2. config.toml 的 provider 覆盖 / 自定义 provider（覆盖内置会 warning）
3. CLI 参数（`-m`、`--thinking`、`--yes` 等）最高

## 源码对照

- 解析与校验：`zenagent/config.py::load_user_config`、`apply_user_config`、`_parse_custom_providers`
- 目录路径：`config_path`、`zenagent/agent/session.py::sessions_dir`、`zenagent/skills.py::default_skill_roots`、
  `zenagent/templates.py::default_template_roots`、`zenagent/mcp/config.py`、`zenagent/cli/keybindings.py::keybindings_path`
- 对应测试：`tests/test_config.py`、`tests/test_catalog.py`、`tests/test_skills.py`、`tests/test_templates.py`
