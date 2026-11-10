# 插件系统实现计划（Plugin System）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「可安装能力包 + 首批三个官方 IM 连接器」落进 zen-agent：声明式 manifest、安装/启停/凭据、免重启装配、RPC 面与桌面管理面板，全程不碰内核。

**Architecture:** 新 `zenagent/plugins/` 扩展面（纯 stdlib）只做「发现/校验/状态/安装」并输出原始 dict；`zenagent/app/plugin_wiring.py` 负责把贡献合并进既有 McpManager / skills / extensions 三条通道；`app/runtime.py` 只增薄钩子与一个新方法 `reconfigure_plugins()`；RPC 增 `PluginsOps` mixin 与 `type: "plugins"`；桌面端新增 Settings → Plugins 目的地。单向调用：zen → 插件 MCP 子进程 → 外部 app，插件无宿主 API。

**Tech Stack:** Python 3.11+（零新依赖，仅 httpx + stdlib）· pytest/pytest-asyncio · React 19 + zustand + Tauri（pnpm, node --test, playwright）· arch-gov manifest/scaffold。

**Spec:** `docs/superpowers/specs/2026-11-10-plugin-system-design.md`（worktree 内同路径；执行时两文件一起读）

## Global Constraints

- 工作目录固定 `/Users/mac/zen-agent/.worktrees/plugin-system`（分支 `feat/plugin-system`）；每条任务末尾 commit。
- 六层依赖只向下（`tests/test_layering.py`）；`zenagent/plugins/` 不得 import `zenagent.mcp / app / cli / agent / tools / providers`（用专门测试钉住）。
- 零新第三方依赖；manifest 用 JSON，不用 YAML。
- 单文件 ≤800 行（`scripts/check_architecture.py`，allowlist 只许降）。宿主顶格文件（runtime.py 996、ops_config.py 940、server.py 787、SettingsPage.tsx 796、appStore.ts 1011、messages.ts 1122）只降不升：新逻辑进新文件，必须触碰处净行数为负或零。
- RPC 契约双向钉住（`tests/test_rpc_contract.py` ↔ `desktop/src/rpc/types.ts` 的 `REQUEST_KINDS`）；`PROTOCOL_VERSION` 不变。
- 凭据只进 `~/.zenagent/settings.json`（0600），键名 `plugin:<id>:<key>`；manifest/项目文件/日志/RPC 回显零出现；`secret: true` 的字段与内嵌 key 的 URL（钉钉/企微）一律掩码。脱敏落点在 `zenagent/mcp/` 错误源头（Task 1），不在展示层。
- 单向调用原则：插件进程不获得任何宿主 API，不反向调用 zen。
- 安装时严格校验；发现时防御复检（坏插件 = 警告 + 跳过，失败隔离）。
- 门禁：Python `scripts/check.sh`；桌面 `cd desktop && pnpm test && pnpm build`。每条任务提交前跑与本任务相关的测试，Task 10 跑全量。
- 事实基准（已核实，勿靠记忆）：`@larksuiteoapi/lark-mcp@0.5.1` 的 `-p` 是 **port**，工具/预设限定用 `-t`（默认 `"preset.default"`）；它支持从环境变量 `APP_ID`/`APP_SECRET`/`LARK_TOOLS` 读默认值（`dist/utils/constants.js` 的 `cleanEnvArgs`）。可用预设：`preset.default / preset.im.default / preset.doc.default / preset.calendar.default / preset.base.default / preset.task.default`。

## Review Focus

按「最可能咬到真实用户」排序；每条都已在对应任务的测试里钉住：

1. **远程 URL 自带密钥**（钉钉/企微、用户自配的远程 MCP）：连接失败时 httpx 异常原文含完整 URL → 必须被 `mcp/` 源头脱敏，warning/RPC/会话记录里看不到 key（Task 1 测试）。
2. **手改坏的 settings.json**（`prefs["plugins"]` 不是 dict、凭据值被写成非字符串）：不能崩、不能把整份设置冲掉，按「默认启用 / 视为未设置」降级（Task 3 测试）。
3. **流式中执行 enable/configure**：状态必须立即持久化，热应用推迟到本轮结束，结果帧带 `apply_pending: true`；不能吞掉持久化、也不能在流式中途换工具（Task 7 测试）。
4. **恶意/破损 zip**（`../` 成员、绝对路径、符号链接、超大解压）：拒绝安装且安装目录零写入（Task 4 测试）。
5. **同名冲突**：用户 `mcp.json` 与插件 server 同名 → 用户优先；两个插件同名 server → 后装覆盖先装并留 warning；均不得静默吞掉（Task 6 测试）。
6. **凭据为空字符串提交**：等同删除，插件回到 `needs_credentials`，空值不得写入 settings（Task 3 测试）。

## 文件地图

| 文件 | 动作 | 职责 |
|---|---|---|
| `zenagent/mcp/redact.py` | 新建 | URL query/userinfo 脱敏 + 文本内 URL 脱敏 |
| `zenagent/mcp/config.py` | 改 | 增公开 `parse_server` 包装 |
| `zenagent/mcp/client.py` `manager.py` | 改 | 错误源头套 `redact_text` |
| `zenagent/plugins/__init__.py` | 新建 | 包说明（导出清单） |
| `zenagent/plugins/manifest.py` | 新建 | `PluginManifest` 解析/严格校验 |
| `zenagent/plugins/state.py` | 新建 | 启停/凭据读写 + `resolve()` 产出贡献 |
| `zenagent/plugins/discovery.py` | 新建 | `discover_plugins()` 失败隔离 |
| `zenagent/plugins/install.py` | 新建 | 目录/zip 安装、卸载、zip 安全 |
| `zenagent/plugins/catalog.py` | 新建 | 内置官方目录读取与安装 |
| `zenagent/plugins/data/<id>/…` | 新建 | 三个连接器 manifest + setup.md + 引导 SKILL.md（包内数据） |
| `zenagent/app/plugin_wiring.py` | 新建 | 合并装配 + `start_mcp()` |
| `zenagent/app/runtime.py` | 改 | 装配薄钩子、`reconfigure_plugins()`、删除 `_start_mcp` |
| `zenagent/cli/rpc/ops_plugins.py` | 新建 | `PluginsOps` mixin（type `plugins`） |
| `zenagent/cli/rpc/server.py` | 改 | import/bases/3 行 dispatch |
| `desktop/src/rpc/types.ts` | 改 | `REQUEST_KINDS` + 插件帧类型 |
| `desktop/src/ui/pages/settingsNav.tsx` | 新建 | 从 SettingsPage 抽出的导航表（腾行数）+ plugins 项 |
| `desktop/src/ui/pages/PluginSettings.tsx`(+css) | 新建 | 官方连接器卡片 / 已装列表 / 配置抽屉 |
| `desktop/src/state/pluginApi.ts` | 新建 | `type: "plugins"` 调用封装（不碰 appStore） |
| `desktop/src/state/stores.ts` | 改 | 增 `usePluginStore` |
| `desktop/src/i18n/messages-plugins.ts` | 新建 | 插件词条（zh/en） |
| `desktop/src/i18n/messages-settings.ts` | 新建 | 抽出的既有 `settings.*` 块（腾行数） |
| `desktop/src/i18n/messages.ts` | 改 | spread 两份新文件（净行数为负） |
| `desktop/src/ui/pages/SettingsPage.tsx` | 改 | 用 settingsNav、lazy 面板、渲染分支 |
| `desktop/e2e/plugin.spec.ts` `fixtures/echo-mcp.py` | 新建 | 端到端（mock MCP server） |
| `docs/arch-gov/*` | 改 | 新增 `plugins` 节点与三条边，scaffold 重生成 |
| `CHANGELOG.md` `docs/guide/plugins.md` | 改/新建 | 架构图小节 + 用户文档 |

---

### Task 1: MCP 错误源头脱敏 + `parse_server` 公开包装

**Files:**
- Create: `zenagent/mcp/redact.py`
- Modify: `zenagent/mcp/config.py`（`__all__` 加 `parse_server`；文件末尾加包装）
- Modify: `zenagent/mcp/client.py`（约 310–320 行的两处 `raise`）
- Modify: `zenagent/mcp/manager.py`（`_connect` 里的 `server.error = …`，约 154 行）
- Test: `tests/test_mcp_redact.py`

**Interfaces:**
- Produces:
  - `redact_url(url: str) -> str` — 掩码 URL 的每个 query 值（`?a=1&b=2` → `?a=***&b=***`）与 userinfo（`https://u:p@h` → `https://***@h`）；无 query 时原样返回。
  - `redact_text(text: str) -> str` — 找出文本中所有 `https?://` 连续 token，逐个套 `redact_url`。
  - `zenagent.mcp.config.parse_server(name: str, raw: object) -> McpServerConfig` — `_parse_server` 的公开包装（Task 6 与安装期严格校验使用）。

- [ ] **Step 1: 写失败测试 `tests/test_mcp_redact.py`**

```python
import httpx
import pytest

from zenagent.mcp.client import HttpTransport, McpError
from zenagent.mcp.config import parse_server
from zenagent.mcp.redact import redact_text, redact_url


def test_redact_url_masks_every_query_value():
    assert (
        redact_url("https://mcp-gw.dingtalk.com/server/x?key=abc123&sig=zzz")
        == "https://mcp-gw.dingtalk.com/server/x?key=***&sig=***"
    )
    assert redact_url("https://example.com/mcp") == "https://example.com/mcp"


def test_redact_url_masks_userinfo():
    assert redact_url("https://user:pass@example.com/mcp") == "https://***@example.com/mcp"


def test_redact_text_masks_embedded_urls():
    message = (
        "Client error '404 Not Found' for url "
        "'https://gw.example.com/server/x?key=topsecret'"
    )
    assert "topsecret" not in redact_text(message)
    assert "https://gw.example.com/server/x?key=***" in redact_text(message)


async def test_http_transport_status_error_never_carries_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="denied key=topsecret")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpTransport("https://gw.example.com/s?key=topsecret", client=client)
    with pytest.raises(McpError) as caught:
        await transport.request("tools/list", None, timeout_s=1.0)
    assert "topsecret" not in str(caught.value)


def test_parse_server_is_a_public_wrapper():
    config = parse_server("s", {"command": ["echo"]})
    assert config.transport_kind == "stdio"
    with pytest.raises(ValueError):
        parse_server("s", {})
    with pytest.raises(ValueError):
        parse_server("s", {"command": ["echo"], "url": "https://x"})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_mcp_redact.py -q`
Expected: FAIL（`ModuleNotFoundError: zenagent.mcp.redact` / `ImportError: parse_server`）

- [ ] **Step 3: 实现**

`zenagent/mcp/redact.py`：两个函数，`_URL_RE = re.compile(r"https?://[^\s'\"<>]+")`；`redact_url` 用 `urllib.parse.urlsplit/urlunsplit`——有 userinfo 则替换为 `***`，query 非空时对每个 `key=value` 输出 `key=***`；`redact_text` 对每个匹配调 `redact_url` 并回填。
`config.py` 末尾：

```python
def parse_server(name: str, raw: object) -> McpServerConfig:
    """Public validator wrapper for plugin manifests (Task 6 assembly)."""
    return _parse_server(name, raw)
```

`client.py` 两处改为：网络异常 `f"http transport error: {redact_text(str(exc))}"`；HTTP ≥400 `f"http {response.status_code}: {redact_text(response.text[:200])}"`。
`manager.py`：`server.error = f"{type(exc).__name__}: {redact_text(str(exc))}"`（`from zenagent.mcp.redact import redact_text`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_mcp_redact.py tests/test_rpc_contract.py -q`
Expected: PASS（契约测试确认未动 wire 形状）

- [ ] **Step 5: Commit**

```bash
git add zenagent/mcp tests/test_mcp_redact.py
git commit -m "fix(mcp): redact credentials at the error source"
```

---

### Task 2: `zenagent/plugins/manifest.py`（严格解析）

**Files:**
- Create: `zenagent/plugins/__init__.py`
- Create: `zenagent/plugins/manifest.py`
- Test: `tests/test_plugin_manifest.py`
- Test: `tests/test_plugin_layering.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) CredentialSpec(key: str, label: str, secret: bool = False, help: str = "", url: str = "")`
  - `@dataclass(frozen=True) PluginManifest(schema_version: int, id: str, name: str, version: str, description: str, mcp_servers: dict[str, dict]  # 未替换占位符的原始 dict, skills: str | None, extension: str | None, credentials: tuple[CredentialSpec, ...], setup_guide: str | None, requires_runtime: str | None, root: Path)`
  - `parse_manifest(root: Path) -> PluginManifest`（读 `root/manifest.json`，严格校验，`ValueError` 带人话消息）
- Consumes: 无（纯 stdlib + `zenagent.plugins` 自身）

校验规则（全部 `ValueError`，消息含字段名，不含密钥值）：
1. 顶层 object；`schemaVersion == 1`；`id` 匹配 `^[a-z0-9][a-z0-9.-]*$` 且 ≤64 字符；`name`/`version`/`description` 非空 str。
2. `contributes` 必须存在且为 object；`mcpServers` 缺省 = `{}`，每个值必须是 object（**形状细节留给装配层**用 `parse_server` 校验，保持 plugins/ 不 import mcp/）。
3. `contributes.skills` / `contributes.extension` / `setup.guide`：相对路径、无 `..` 段、`(root / value).resolve()` 必须仍在 `root.resolve()` 内；声明了 `extension` 则文件必须存在。
4. `credentials`：list[object]，`key` 匹配 `^[A-Z][A-Z0-9_]*$`、去重，`label` 非空，`secret` 缺省 `False`，`help`/`url` 可空 str。
5. 占位符：扫描 `mcpServers` 中全部字符串（command 每个元素、env/headers 的每个值、url），正则 `\$\{([A-Z0-9_]+)\}`；每个 key 必须在 credentials 声明里，否则报「undeclared placeholder ${KEY}」。
6. `setup.requiresRuntime` 可空 str；`manifest.json` 不存在/非 JSON → `ValueError`。

- [ ] **Step 1: 写失败测试 `tests/test_plugin_manifest.py`**

Fixture（写进 tmp_path 的 `manifest.json`，与 Task 5 真实数据同形）：

```python
VALID = {
    "schemaVersion": 1,
    "id": "zen.feishu",
    "name": "飞书",
    "version": "0.1.0",
    "description": "飞书官方 MCP 连接器",
    "contributes": {
        "mcpServers": {
            "feishu": {
                "command": ["npx", "-y", "@larksuiteoapi/lark-mcp", "-t", "preset.im.default"],
                "env": {"APP_ID": "${APP_ID}", "APP_SECRET": "${APP_SECRET}"},
            }
        },
        "skills": "skills",
        "extension": None,
    },
    "credentials": [
        {"key": "APP_ID", "label": "App ID", "secret": False},
        {"key": "APP_SECRET", "label": "App Secret", "secret": True},
    ],
    "setup": {"guide": "setup.md", "requiresRuntime": "Node.js 18+（npx）"},
}

def write(root, data): ...  # (root / "manifest.json").write_text(json.dumps(data))

def test_valid_manifest_parses(tmp_path):
    write(tmp_path, VALID)
    (tmp_path / "skills" / "feishu-guide").mkdir(parents=True)
    manifest = parse_manifest(tmp_path)
    assert manifest.id == "zen.feishu"
    assert manifest.mcp_servers["feishu"]["env"]["APP_SECRET"] == "${APP_SECRET}"
    assert [c.secret for c in manifest.credentials] == [False, True]
    assert manifest.requires_runtime == "Node.js 18+（npx）"

def test_unknown_placeholder_rejected(tmp_path):   # 加 "${MCP_URL}" 但未声明 → ValueError match "MCP_URL"
def test_skills_path_escape_rejected(tmp_path):    # skills="/abs" 与 "../x" → ValueError
def test_bad_id_rejected(tmp_path):                # "Zen.Feishu" → ValueError
def test_schema_version_pinned(tmp_path):          # 2 → ValueError
def test_duplicate_credential_keys_rejected(tmp_path)
def test_declared_extension_must_exist(tmp_path)   # extension="ext.py" 未落盘 → ValueError
def test_missing_manifest_file_rejected(tmp_path)  # ValueError
```

**`tests/test_plugin_layering.py`**：

```python
def test_plugins_package_imports_only_base_modules():
    forbidden = ("zenagent.mcp", "zenagent.app", "zenagent.cli", "zenagent.agent",
                 "zenagent.tools", "zenagent.providers")
    violations = []
    for path in (ROOT / "zenagent" / "plugins").rglob("*.py"):
        for module in imports_of(path):
            if module.startswith(forbidden):
                violations.append(f"{path.name}: {module}")
    assert violations == []
```

（`imports_of` 复制 `tests/test_layering.py::_imports` 的 ast 逻辑。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_plugin_manifest.py -q`
Expected: FAIL（`ModuleNotFoundError: zenagent.plugins`）

- [ ] **Step 3: 实现 `manifest.py`**

按规则 1–6 实现；`_contained(root, value)` 帮助函数做路径检查；占位符扫描用 `_scan_placeholders(servers)` 递归所有 str。`__init__.py` 只写 docstring 与 `__all__`（暂空列表，Task 3 补）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_plugin_manifest.py tests/test_plugin_layering.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/plugins tests/test_plugin_manifest.py tests/test_plugin_layering.py
git commit -m "feat(plugins): strict manifest parsing"
```

---

### Task 3: `zenagent/plugins/state.py`（启停/凭据/贡献解析）

**Files:**
- Create: `zenagent/plugins/state.py`
- Modify: `zenagent/plugins/__init__.py`（导出）
- Test: `tests/test_plugin_state.py`

**Interfaces:**
- Produces:
  - `PLUGIN_STATE_PREF = "plugins"`
  - `credential_name(plugin_id: str, key: str) -> str` → `f"plugin:{plugin_id}:{key}"`
  - `is_enabled(plugin_id: str) -> bool` / `set_enabled(plugin_id: str, enabled: bool) -> None`
  - `credentials_for(plugin_id: str) -> dict[str, str]`（**直接读 `settings.load().api_keys`**，不走 `get_api_key`：插件凭据不得被环境变量顶替）
  - `set_credentials(plugin_id: str, values: dict[str, str]) -> None`（strip；空串=删除）
  - `clear_plugin(plugin_id: str) -> None`（删 `prefs["plugins"][id]` 与该命名空间全部 `api_keys`）
  - `@dataclass ResolvedContributions(servers: dict[str, dict], skill_roots: list[Path], extension: Path | None, missing: list[str])`
  - `resolve(manifest: PluginManifest, credentials: dict[str, str]) -> ResolvedContributions`
- Consumes: `zenagent.settings`、`zenagent.plugins.manifest.PluginManifest`

`resolve` 规则：
- `missing = [spec.key for spec in manifest.credentials if not credentials.get(spec.key)]`；非空 → `servers={}`（skill_roots/extension 仍给，引导 skill 不依赖凭据）。
- 否则替换占位符：`re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: credentials.get(m.group(1), ""), value)`，作用于 command 每个元素、env/headers 每个值、url。
- server 名统一 `f"{manifest.id}.{key}"`（无特例）。
- `skill_roots`：`manifest.root / manifest.skills` 且 `is_dir()`；`extension`：`manifest.root / manifest.extension` 且存在。

- [ ] **Step 1: 写失败测试 `tests/test_plugin_state.py`**

用 `monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))` + `settings.clear_cache()`（fixture 里 autouse）。

```python
def test_enabled_defaults_true_and_roundtrips():
    assert is_enabled("zen.feishu") is True
    set_enabled("zen.feishu", False)
    assert is_enabled("zen.feishu") is False
    assert settings.load().prefs["plugins"]["zen.feishu"]["enabled"] is False

def test_corrupt_plugin_state_degrades_to_enabled():
    # prefs["plugins"] 是字符串（手改坏）→ is_enabled True，不抛
    store = settings.load(); store.prefs["plugins"] = "oops"; settings.save(store)
    assert is_enabled("x") is True
    set_enabled("x", False)  # 重建 dict 后写入成功
    assert is_enabled("x") is False

def test_credentials_use_namespaced_keys():
    set_credentials("zen.feishu", {"APP_ID": " cli_1 ", "APP_SECRET": "sec"})
    assert settings.load().api_keys["plugin:zen.feishu:APP_ID"] == "cli_1"

def test_empty_value_deletes_credential():
    set_credentials("zen.feishu", {"APP_ID": "cli_1"})
    set_credentials("zen.feishu", {"APP_ID": "  "})
    assert "plugin:zen.feishu:APP_ID" not in settings.load().api_keys

def test_credentials_ignore_environment(monkeypatch):
    monkeypatch.setenv("APP_ID", "from-env")
    assert credentials_for("zen.feishu") == {}

def test_resolve_missing_credentials_suppresses_servers():
    manifest = manifest_for(...)  # 与 Task 2 fixture 同形
    resolved = resolve(manifest, {"APP_ID": "cli_1"})
    assert resolved.missing == ["APP_SECRET"]
    assert resolved.servers == {}

def test_resolve_substitutes_and_names_servers():
    resolved = resolve(manifest, {"APP_ID": "cli_1", "APP_SECRET": "sec"})
    server = resolved.servers["zen.feishu.feishu"]
    assert server["env"]["APP_SECRET"] == "sec"
    assert resolved.missing == []
```

- [ ] **Step 2: 跑测试确认失败** → Run: `uv run pytest tests/test_plugin_state.py -q`；Expected: FAIL

- [ ] **Step 3: 实现 `state.py`**，按上面规则与签名。

- [ ] **Step 4: 跑测试确认通过** → Run: `uv run pytest tests/test_plugin_state.py -q`；Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/plugins tests/test_plugin_state.py
git commit -m "feat(plugins): enable state and namespaced credentials"
```

---

### Task 4: 发现 + 安装/卸载（zip 安全）

**Files:**
- Create: `zenagent/plugins/discovery.py`
- Create: `zenagent/plugins/install.py`
- Modify: `zenagent/plugins/__init__.py`（导出）
- Modify: `tests/conftest.py`（新增 autouse fixture `isolated_plugins_root`：monkeypatch `zenagent.plugins.discovery.installed_root` → `tmp_path/"plugins"`；否则 Runtime 测试会扫开发者真实 `~/.zenagent/plugins`）
- Test: `tests/test_plugin_install.py`

**Interfaces:**
- Produces:
  - `installed_root() -> Path` → `Path.home() / ".zenagent" / "plugins" / "installed"`
  - `discover_plugins(root: Path | None = None) -> tuple[list[PluginManifest], list[str]]` — 遍历子目录中带 `manifest.json` 者；`ValueError` → warning `f"plugin {dir.name}: {exc}"` 并跳过；按 id 排序。
  - `MAX_PLUGIN_BYTES = 10 * 1024 * 1024`、`MAX_ZIP_MEMBERS = 500`
  - `install_plugin(source: Path | str, *, root: Path | None = None) -> PluginManifest`
  - `uninstall_plugin(plugin_id: str, *, root: Path | None = None) -> None`
- Consumes: `manifest.parse_manifest`、`state.clear_plugin`

`install_plugin` 规则：
- 目录源：必须含 `manifest.json`，`parse_manifest` 严格通过；复制到 `root/.install-<uuid>` 后 `shutil.rmtree(target, ignore_errors=True)` + `os.replace(temp, target)`（重装/升级 = 原子换入）。
- zip 源：`zipfile.ZipFile` 逐成员校验——拒绝绝对路径、任一组件为 `..`、符号链接位（`(info.external_attr >> 16) & 0o170000 == 0o120000`）、成员数 > `MAX_ZIP_MEMBERS`、`sum(info.file_size) > MAX_PLUGIN_BYTES`；解压到临时目录后按目录安装。兼容「zip 内只有一个顶层目录」的情形（GitHub 打包）：若解压根只含一个目录且其下有 `manifest.json`，取其为插件根。
- 一切失败在写入 `installed/` 前发生；失败后临时目录清理。
- `uninstall_plugin`：`shutil.rmtree(root/<id>)`（不存在 → ValueError），然后 `state.clear_plugin(id)`。

- [ ] **Step 1: 写失败测试 `tests/test_plugin_install.py`**

```python
def _make_plugin(dirpath, plugin_id="zen.demo"): ...  # manifest + skills/demo-guide/SKILL.md

def test_discover_skips_broken_manifest(tmp_path):
    _make_plugin(tmp_path / "a")
    (tmp_path / "b").mkdir(); (tmp_path / "b" / "manifest.json").write_text("{not json")
    manifests, warnings = discover_plugins(tmp_path)
    assert [m.id for m in manifests] == ["zen.demo"] and len(warnings) == 1

def test_install_directory_and_reinstall(tmp_path):
    src = tmp_path / "src"; _make_plugin(src)
    manifest = install_plugin(src, root=tmp_path / "installed")
    assert (tmp_path / "installed" / "zen.demo" / "manifest.json").is_file()
    (src / "setup.md").write_text("v2")
    install_plugin(src, root=tmp_path / "installed")          # 重装覆盖
    assert (tmp_path / "installed" / "zen.demo" / "setup.md").read_text() == "v2"

def test_install_requires_manifest(tmp_path):
    with pytest.raises(ValueError):
        install_plugin(tmp_path, root=tmp_path / "installed")

def test_zip_traversal_rejected_and_nothing_written(tmp_path):
    # zip 成员 ../evil.txt → ValueError；root 下无写入
def test_zip_absolute_member_rejected(tmp_path)   # /tmp/evil.txt
def test_zip_size_cap_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "MAX_PLUGIN_BYTES", 10)
    ...
def test_zip_single_toplevel_directory_installs(tmp_path)  # 打包成 zen.demo/... 
def test_uninstall_removes_dir_and_credentials(tmp_path):
    # ZENAGENT_SETTINGS_PATH fixture；装→配凭据→卸载→目录没了、api_keys 里 plugin: 前缀清空
```

- [ ] **Step 2: 跑测试确认失败** → Expected: FAIL

- [ ] **Step 3: 实现 `discovery.py` 与 `install.py`**，按上面规则；同时在 `tests/conftest.py` 加 `isolated_plugins_root` autouse fixture（与既有 `isolated_settings` 同款模式）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_plugin_install.py tests/test_plugin_layering.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/plugins tests/test_plugin_install.py
git commit -m "feat(plugins): discovery and safe install/uninstall"
```

---

### Task 5: 内置目录 + 三个官方连接器数据

**Files:**
- Create: `zenagent/plugins/catalog.py`
- Create: `zenagent/plugins/data/zen.dingtalk/{manifest.json,setup.md,skills/dingtalk-guide/SKILL.md}`
- Create: `zenagent/plugins/data/zen.feishu/{manifest.json,setup.md,skills/feishu-guide/SKILL.md}`
- Create: `zenagent/plugins/data/zen.wecom/{manifest.json,setup.md,skills/wecom-guide/SKILL.md}`
- Modify: `docs/superpowers/specs/2026-11-10-plugin-system-design.md` §6 示例（见下）
- Test: `tests/test_plugin_catalog.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) CatalogEntry(id: str, name: str, description: str, requires_runtime: str | None)`
  - `builtin_catalog() -> list[CatalogEntry]` — 用 `importlib.resources.files("zenagent.plugins").joinpath("data")` 列目录，逐个 `parse_manifest`（严格；损坏 = 打包缺陷，抛 `RuntimeError("builtin catalog corrupt: …")`）
  - `install_builtin(plugin_id: str, *, root: Path | None = None) -> PluginManifest` — id 不在目录 → `ValueError`；`importlib.resources.as_file(data_dir / id)` 后走 `install_plugin`

三份 manifest 的关键内容（其余字段照 Task 2 形状）：

| id | mcpServers | credentials | requiresRuntime |
|---|---|---|---|
| `zen.dingtalk` | `{"dingtalk": {"url": "${MCP_URL}"}}` | `MCP_URL`（secret，label「MCP 服务 URL」，url `https://mcp.dingtalk.com`） | null |
| `zen.feishu` | `{"feishu": {"command": ["npx","-y","@larksuiteoapi/lark-mcp","-t","preset.im.default,preset.doc.default,preset.calendar.default"], "env": {"APP_ID":"${APP_ID}","APP_SECRET":"${APP_SECRET}"}}}` | `APP_ID`（非 secret）、`APP_SECRET`（secret） | `Node.js 18+（npx）` |
| `zen.wecom` | `{"wecom": {"url": "${MCP_URL}"}}` | `MCP_URL`（secret） | null |

- 飞书用 **env 传凭据**（已核实 lark-mcp 读 `APP_ID`/`APP_SECRET`），避免凭据出现在子进程 argv（`ps` 可见）；工具集用 `-t` 而非 `-p`——**设计文档 §6 示例的 `"-p", "preset.im.default"` 是事实错误（`-p` 是 port），一并改为 `-t` 多预设**。
- 钉钉 setup.md：广场生成远程 URL 的步骤 + 「整条 URL 即长效凭据，勿贴入项目文件」提示。
- 企微 setup.md：管理页创建智能机器人、逐项授权、扫码后复制 URL；**置顶**「文档类授权 7 天到期，连接失败时重新授权」。
- 飞书 setup.md：开放平台建应用 → 取 App ID/Secret → 开通消息/文档/日历权限；注明厂商仍标注 Beta。
- 每份引导 SKILL.md：工具命名规则（`mcp__<sanitized>__<tool>`）、常用流程（发消息/查文档）、授权排障（401 → 回 setup 重新授权）。

- [ ] **Step 1: 写失败测试 `tests/test_plugin_catalog.py`**

```python
def test_builtin_catalog_lists_three_connectors():
    entries = builtin_catalog()
    assert [e.id for e in entries] == ["zen.dingtalk", "zen.feishu", "zen.wecom"]

def test_builtin_manifests_parse_and_declare_their_placeholders():
    for entry in builtin_catalog():
        manifest = parse_manifest(catalog_dir(entry.id))
        declared = {spec.key for spec in manifest.credentials}
        assert all(key in declared for key in placeholders_of(manifest.mcp_servers))

def test_feishu_uses_tools_flag_not_port_flag():
    manifest = parse_manifest(catalog_dir("zen.feishu"))
    command = manifest.mcp_servers["feishu"]["command"]
    assert "-t" in command and "-p" not in command
    assert manifest.mcp_servers["feishu"]["env"]["APP_SECRET"] == "${APP_SECRET}"

def test_install_builtin_copies_tree(tmp_path):
    install_builtin("zen.wecom", root=tmp_path / "installed")
    assert (tmp_path / "installed" / "zen.wecom" / "skills" / "wecom-guide" / "SKILL.md").is_file()

def test_unknown_builtin_id_rejected(tmp_path):
    with pytest.raises(ValueError):
        install_builtin("zen.nope", root=tmp_path / "installed")
```

（`catalog_dir` 作为 `catalog.py` 私有 `_catalog_dir` 暴露给测试即可，或测试直接用 `importlib.resources`。）

- [ ] **Step 2: 跑测试确认失败** → Expected: FAIL

- [ ] **Step 3: 实现 `catalog.py` 与全部数据文件**

数据文件是包内资源（`uv_build` 默认收录模块目录下所有文件，`zenagent/catalog.yaml` 是既有先例，无需改 pyproject）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_plugin_catalog.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zenagent/plugins docs/superpowers/specs/2026-11-10-plugin-system-design.md tests/test_plugin_catalog.py
git commit -m "feat(plugins): builtin catalog with three official connectors"
```

---

### Task 6: 装配层 `plugin_wiring.py` + Runtime 接线

**Files:**
- Create: `zenagent/app/plugin_wiring.py`
- Modify: `zenagent/app/runtime.py`（`__init__` ~164 行、bootstrap ~385–410、`rediscover` ~860、删除 `_start_mcp` 529–541、新增 `reconfigure_plugins`）
- Test: `tests/test_plugin_wiring.py`
- Test: `tests/test_plugin_runtime.py`

**Interfaces:**
- Produces（`plugin_wiring`）：
  - `@dataclass PluginAssembly(configs: dict[str, McpServerConfig], skill_roots: list[Path], extension_files: list[str], statuses: dict[str, str], warnings: list[str])`
  - `assemble_plugins(*, user_configs: dict[str, McpServerConfig] | None = None) -> PluginAssembly`
    - `discover_plugins()` → 过滤 `state.is_enabled` → `state.resolve(manifest, state.credentials_for(id))` → 合并：`merged = {**plugin_servers, **user_configs}`（用户同名优先）；插件间同名按 id 排序后者覆盖并 append warning。
    - `statuses[id]` ∈ `ready`（启用且有凭据）/ `needs_credentials` / `disabled`；缺凭据时同时 append warning `f"{id}: missing credentials APP_SECRET"`（spec §7.1：跳过并记 warning）。
    - 每个 plugin server 用 `config.parse_server(name, raw)` 复检；坏形状 → warning + 跳过（失败隔离）。
  - `attach_plugins(runtime, options) -> PluginAssembly` — bootstrap 专用：按 `options.mcp`/`options.mcp_servers` 造 `user_configs`、调 `assemble_plugins`、把三个结果写到 runtime、把 warnings 记入 `runtime.warnings`。
  - `async def start_mcp(configs, *, retry_interval_s: float) -> McpManager | None` — 空配置返回 None，否则新建 `McpManager` 并 `await start()`，warning 前缀 `mcp `。
  - `async def reconfigure(runtime) -> list[str]` — **方法主体放这里**（body 里用 `TYPE_CHECKING` 导入 Runtime，避免 runtime→wiring→runtime 循环）：流式中 `raise RuntimeError(t("cmdmsg.cannot_reload"))`；重算 assembly → 新 manager 起好后再关旧（swap-and-close）→ 更新三条通道 → `runtime.rediscover()`（复用其 skills 重建与 `_recompose`）。
- Produces（Runtime）：
  - `runtime.plugin_statuses: dict[str, str]`、`runtime._plugin_skill_roots: list[Path]`、`runtime._plugin_extension_files: list[str]`（`__init__` 初始化）
  - `async def reconfigure_plugins(self) -> list[str]` — **3 行委托**：`return await reconfigure(self)`。方法体不进 runtime.py（预算原因，见下）。
- Consumes: Task 1 `parse_server`；Task 2–5 全部；`mcp.manager.McpManager`、`mcp.config.load_mcp_config`

bootstrap 改动（行号按当前文件；`load_mcp_config`/`McpManager` 的 import 一并从 runtime.py 移除，交给 wiring）：
1. `__init__`（`self.templates` 之后）加三个初始值（+3 行）。
2. 原 393–394 两行 → 五行（`assembly` 必须先绑定：`--tools` 自定义工具时下面的 extension 块仍会执行）：

```python
        assembly = None
        if options.tools is None:
            assembly = attach_plugins(runtime, options)
            if options.mcp:
                runtime.mcp = await start_mcp(assembly.configs, retry_interval_s=options.mcp_retry_interval_s)
```

3. 删除 `_start_mcp` 方法（529–541，含前导空行 −14）。
4. 397 → `discover_skills([*default_skill_roots(Path.cwd()), *assembly.skill_roots])`（可能折行，+1）。
5. 407–410 的列表追加 `*(assembly.extension_files if assembly else [])`（同一行，±0）。
6. `rediscover` 860 → `discover_skills([*default_skill_roots(Path.cwd()), *self._plugin_skill_roots])`（折行 +1）。
7. 末尾加委托方法：空行 + def + docstring + return = +4。

预算核算（checker 按 `splitlines()` 计全部行）：+3 +3 −14 +1 +0 +1 +4 = **−2** → runtime.py 996 → 994，满足「只降不升」。

`reconfigure(runtime)` 主体（plugin_wiring.py 内）：

```python
async def reconfigure(runtime) -> list[str]:
    agent = getattr(runtime, "agent", None)
    if agent is not None and agent.is_streaming:
        raise RuntimeError(t("cmdmsg.cannot_reload"))
    options = getattr(runtime, "_options", None)
    user_configs = {}
    if options is None or options.mcp:
        user_configs = load_mcp_config(getattr(options, "mcp_file", None))
        if options is not None and options.mcp_servers:
            user_configs = {n: c for n, c in user_configs.items() if n in options.mcp_servers}
    assembly = assemble_plugins(user_configs=user_configs)
    old, runtime.mcp = runtime.mcp, None
    runtime.plugin_statuses = assembly.statuses
    runtime._plugin_skill_roots = assembly.skill_roots
    runtime._plugin_extension_files = assembly.extension_files
    if options is None or options.mcp:
        retry = getattr(options, "mcp_retry_interval_s", None) or 30.0
        runtime.mcp = await start_mcp(assembly.configs, retry_interval_s=retry)
    if old is not None:
        await old.close()
    for warning in assembly.warnings:
        runtime._note(f"plugins {warning}")
    runtime.rediscover()  # 插件带来/撤走 skill 与 extension；流式已被上面挡住
    ready = sum(1 for state in runtime.plugin_statuses.values() if state == "ready")
    return [f"{len(runtime.plugin_statuses)} plugins ({ready} ready)"]
```

- [ ] **Step 1: 写失败测试**

`tests/test_plugin_wiring.py`：

```python
def test_user_servers_win_over_plugin_servers(tmp_path, monkeypatch):  # HOME→tmp，装插件+写 mcp 同名 server
def test_disabled_plugin_is_excluded(tmp_path)
def test_missing_credentials_sets_status_without_servers(tmp_path)
def test_bad_plugin_server_shape_warns_and_skips(tmp_path)  # url+command 同时出现
```

`tests/test_plugin_runtime.py`（`runtime_factory` 复用 conftest（默认 `mcp=False`，插件 server 不启动、skill 仍装配）；涉及 server 的用例传 `mcp=True` 并 `monkeypatch.setattr(plugin_wiring, "start_mcp", fake)` 返回桩对象，桩记录 configs）：

```python
async def test_bootstrap_installs_plugin_skills_and_servers(runtime_factory, ...)
async def test_bootstrap_without_credentials_skips_servers(runtime_factory, ...)
async def test_reconfigure_swaps_manager_and_closes_old(runtime_factory, ...)
async def test_reconfigure_refuses_while_streaming(runtime_factory, ...)  # agent.is_streaming 置 True → RuntimeError
```

- [ ] **Step 2: 跑测试确认失败** → Expected: FAIL

- [ ] **Step 3: 实现 wiring + runtime 改动**（按上面的预算核算执行：删 `_start_mcp`、bootstrap 四行、委托方法；改完立刻跑 `python3 scripts/check_architecture.py`，runtime.py 必须 ≤996 且 allowlist 不放宽）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_plugin_wiring.py tests/test_plugin_runtime.py tests/test_layering.py -q && python3 scripts/check_architecture.py`
Expected: PASS，架构预算通过

- [ ] **Step 5: Commit**

```bash
git add zenagent/app tests/test_plugin_wiring.py tests/test_plugin_runtime.py
git commit -m "feat(app): assemble plugin contributions into the runtime"
```

---

### Task 7: RPC `type: "plugins"` + 契约

**Files:**
- Create: `zenagent/cli/rpc/ops_plugins.py`
- Modify: `zenagent/cli/rpc/server.py`（import + bases + dispatch，约 3+2 行）
- Modify: `desktop/src/rpc/types.ts`（`REQUEST_KINDS` 插 `'plugins'`；帧类型）
- Test: `tests/test_rpc_plugins.py`

**Interfaces:**
- Produces: `class PluginsOps`（mixin）：
  - `async def _plugins(self, request: dict, request_id) -> None`
  - `_plugins_result(self, request_id, *, apply_pending: bool = False) -> dict`
  - `_plugins_rows(self) -> list[dict]`、`_catalog_rows(self) -> list[dict]`
  - `async def _apply_or_defer(self) -> bool` — `runtime.reconfigure_plugins()` 成功 True；`RuntimeError` → `asyncio.create_task(self._apply_when_idle())`，False
  - `async def _apply_when_idle(self)` — 每 0.5s 轮询 `runtime.agent.is_streaming`，结束后重试一次 reconfigure（异常只 `logger.warning`）
- Wire 形状：

```json
// result: type "result", ok true
{"plugins": [{"id","name","version","description","enabled","status",
  "credentials":[{"key","label","secret","help","url","set"}],
  "servers":[{"name","state","tools","error"}]}],
 "catalog": [{"id","name","description","requires_runtime","installed"}],
 "problem": null, "apply_pending": false}
```

全部字段 **snake_case**（与既有 RPC 风格一致：`protocol_version`、`env_keys`）——TS 接口与结果帧逐字段同名，不做 camelCase 转译。

status 推导：`disabled` > `needs_credentials` > 任一 server `failed` → `failed` > 任一 `connected` → `connected` > `ready`。`error` 来自 manager（已在 Task 1 脱敏）；**url/env/凭据值永不出现在帧里**。
ops：`list` / `install`（`{id}` 或 `{path}`，都没有 → `{"type":"error","message":"plugins install needs an id or path"}`）/ `uninstall` / `enable` / `disable` / `configure`（`{id, values:{KEY: value}}`，空串=删除）。写操作统一 `failure_frame(request_id, code="plugin_failed", message="plugin failed", exc=exc, op=op)`；成功后 `_plugins_result(..., apply_pending=not applied)`。
- Consumes: `zenagent.plugins.{discovery,install,catalog,state,manifest}`；`self.runtime`（可为 None 时 list 只读目录、写操作报错）；`errors.failure_frame`

server.py dispatch 插在 `kind == "mcp"` 之前（契约测试扫描全部 `kind == "..."`，无遗漏）：

```python
if kind == "plugins":
    await self._plugins(request, request_id)
    return True
```

`types.ts`：`REQUEST_KINDS` 按字母序在 `'models'` 与 `'project'` 之间插 `'plugins'`；增 `PluginRow`、`PluginCatalogRow`、`PluginsResult`（字段与帧逐字同名，snake_case：`requires_runtime`、`apply_pending`）。契约测试只钉 `REQUEST_KINDS`/`PROTOCOL_VERSION`，类型仍须与真实帧一致。

- [ ] **Step 1: 写失败测试 `tests/test_rpc_plugins.py`**

（fixture：`runtime_factory` 在 `tests/conftest.py`，默认 `mcp=False/skills=False/templates=False`；`isolated_settings` 与 Task 4 的 `isolated_plugins_root` 已自动生效。`runtime.reconfigure_plugins` 用 AsyncMock 替换，避免真实连接；`_ok_stream` 从 `tests/test_rpc_contract.py` 复制。）

```python
async def test_plugins_list_exposes_catalog_without_secret_values(...)
    # frame["catalog"] 三个 id；"APP_SECRET" in credentials 但无任何值字段

async def test_install_enable_configure_roundtrip(...)
    # install {id:"zen.feishu"} → status needs_credentials
    # configure {values:{APP_ID:"cli_1", APP_SECRET:"topsecret"}} → ok；帧文本不含 "topsecret"
    # settings.json 里出现 plugin:zen.feishu:APP_ID

async def test_apply_defers_while_streaming(...)
    # reconfigure_plugins 抛 RuntimeError → frame["apply_pending"] is True

async def test_disable_then_enable_roundtrip(...)
async def test_uninstall_clears_state(...)
async def test_unknown_op_reports_error_frame(...)   # type error, message mentions op
async def test_dispatch_accepts_plugins_kind(...)     # 走 RpcServer._dispatch，收到 result
```

- [ ] **Step 2: 跑测试确认失败** → Expected: FAIL

- [ ] **Step 3: 实现 ops_plugins + dispatch + types.ts**

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_rpc_plugins.py tests/test_rpc_contract.py tests/test_layering.py -q && python3 scripts/check_architecture.py`
Expected: PASS（server.py ≤800，ops_config.py 未动）

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/rpc desktop/src/rpc/types.ts tests/test_rpc_plugins.py
git commit -m "feat(rpc): plugins list/install/enable/configure surface"
```

---

### Task 8: 桌面端 Settings → Plugins

**Files:**
- Create: `desktop/src/ui/pages/settingsNav.tsx`（把 `Tab`/`NavGroup`/`NavItem`/`NAV_ITEMS` 从 SettingsPage.tsx 整体搬出，导出；`Tab` 加 `'plugins'`，`NAV_ITEMS` 在 `mcp` 后加一项，group `resources`，图标用 `TerminalIcon` 或既有合适图标）
- Modify: `desktop/src/ui/pages/SettingsPage.tsx`（import 导航；删除原定义；`const PluginSettings = lazy(...)`；渲染分支 +1；净减行数）
- Create: `desktop/src/ui/pages/PluginSettings.tsx`、`PluginSettings.module.css`
- Create: `desktop/src/state/pluginApi.ts`
- Modify: `desktop/src/state/stores.ts`（+`usePluginStore`，模式同 `useMcpStore`）
- Create: `desktop/src/i18n/messages-plugins.ts`（zh/en 两份插件词条）
- Create: `desktop/src/i18n/messages-settings.ts`（把 messages.ts 的 `settings.*` 全块搬出，保持字节一致）
- Modify: `desktop/src/i18n/messages.ts`（顶部 import 两份新文件，`zh`/`en` 各 spread；净行数为负以守住 allowlist 1122）

**Interfaces:**
- Produces:
  - `pluginApi.ts`：

```ts
export async function listPlugins(): Promise<PluginsResult>
export async function installPlugin(source: { id?: string; path?: string }): Promise<PluginsResult>
export async function uninstallPlugin(id: string): Promise<PluginsResult>
export async function setPluginEnabled(id: string, enabled: boolean): Promise<PluginsResult>
export async function configurePlugin(id: string, values: Record<string, string>): Promise<PluginsResult>
```

  每个都用 `useAppStore.getState().client().send({ type: 'plugins', op, ... })`，直接把帧返回（错误处理在 UI，与 McpSettings 一致）。
  - `stores.ts`：

```ts
export interface PluginState {
  plugins: PluginRow[]
  catalog: PluginCatalogRow[]
  problem: string | null
  applyPending: boolean
  setView(plugins: PluginRow[], catalog: PluginCatalogRow[], problem: string | null, applyPending?: boolean): void
}
export const usePluginStore = create<PluginState>(...)
```
  - `PluginSettings.tsx`：挂载即 `listPlugins()`；官方连接器三卡片（描述 + `requires_runtime` 提示 + 安装按钮）；**安装成功后若该插件 `needs_credentials`，自动打开它的配置抽屉**（spec §9 交互闭环）；已装列表（启停开关、状态徽标 `settings.pluginsStatus*`、失败摘要、配置按钮、卸载两段式确认并明示「将删除凭据」）；配置抽屉按 `credentials` 生成表单（`secret` 用 type=password，`set` 显示已设置、占位符「已保存，留空保持不变」；help 链接 `url`）。
  - i18n 词条前缀 `settings.plugins*`（标题、区段、按钮、状态、确认、错误兜底），zh/en 同键；`applyPending` 为真时顶部显示提示条 `settings.pluginsApplyPending`（文案「本轮结束后生效」，spec §7.2）。

- [ ] **Step 1: 先改 i18n 与导航（可编译的最小面）**

搬 `messages.ts` 的 `settings.*` 块到 `messages-settings.ts`（逐字节复制）；写 `messages-plugins.ts`；`messages.ts` spread 两份。跑 `cd desktop && pnpm test`（zh/en parity 通过，`node --test` 直接加载）与 `python3 scripts/check_architecture.py`（messages.ts 行数下降）。

- [ ] **Step 2: 实现 settingsNav + SettingsPage 接线，跑 `pnpm build`**（`tsc --noEmit` 抓类型）

- [ ] **Step 3: 实现 pluginApi + usePluginStore + PluginSettings 面板（含 css）**

按 McpSettings 的模式：`useT`/`useLocale`、`frameErrorNotice(frame, locale)`、armed 删除、busy 态、`css` module。安装本地插件用文本框 + 「安装」按钮（接受目录或 zip 路径），失败走 `frameErrorNotice`。

- [ ] **Step 4: 门禁 + 手动冒烟**

Run: `cd desktop && pnpm test && pnpm build && cd .. && python3 scripts/check_architecture.py`
Expected: 全 PASS；SettingsPage.tsx/messages.ts 行数低于 allowlist 现值
冒烟：`zen dev`（或既有桌面启动脚本）打开 Settings → Plugins，三卡片可见，安装 zen.wecom 出现在列表并显示「待配置」。

- [ ] **Step 5: Commit**

```bash
git add desktop/src
git commit -m "feat(desktop): plugins settings panel"
```

---

### Task 9: 端到端（mock MCP）+ 收口

**Files:**
- Create: `desktop/e2e/fixtures/echo-mcp.py`（stdlib 实现的极简 MCP stdio server：`initialize`、`tools/list` 返回 `echo`、`tools/call` 回显；长驻读 stdin）
- Create: `desktop/e2e/fixtures/demo-plugin/manifest.json`（command 指向 `python3 <fixture>/echo-mcp.py`，一个凭据 `DEMO_TEXT`，`${DEMO_TEXT}` 进 env）
- Create: `desktop/e2e/plugin.spec.ts`
- Modify: `desktop/e2e/` 启动配置（如需把 HOME/settings 指向临时目录——跟随 `harness.ts` 既有机制，不扩 harness 行数则用 spec 内 env 传递）

**Interfaces:**
- Consumes: Task 7 RPC、Task 8 UI、`zenagent/plugins/install.py`

测试流（一条 spec）：
1. 启动应用（临时 HOME）→ Settings → Plugins。
2. 三张官方卡片可见。
3. 用本地安装入口指向 `fixtures/demo-plugin` → 列表出现，状态「待配置」。
4. 安装后配置抽屉自动打开（spec §9），填 `DEMO_TEXT=hi` → 保存 → 状态变「已连接」（mock server 连上）。
5. 关闭重开设置面板，状态保持（持久化）。
6. 卸载（两段式）→ 从列表消失。

- [ ] **Step 1: 写 spec 与 fixtures**
- [ ] **Step 2: 跑 `cd desktop && pnpm e2e -- plugin.spec.ts`，确认失败（UI 未就绪时）或直接走到通过**
- [ ] **Step 3: 修到通过**（选择器优先 `getByRole`/既有 e2e 习惯；等待用 `expect.poll`，不用 sleep）
- [ ] **Step 4: 全量门禁**

Run: `scripts/check.sh && cd desktop && pnpm test && pnpm build`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add desktop/e2e
git commit -m "test(e2e): plugin install and configure flow"
```

---

### Task 10: 治理与文档

**Files:**
- Modify: `docs/arch-gov/manifest.json`（先从主检出 `/Users/mac/zen-agent/docs/arch-gov/` 复制进 worktree——主检出里该目录未跟踪）
- Regenerate: `docs/arch-gov/{brief.md,nodes.md,graph.html}`（`python3 /Users/mac/.agents/skills/arch-gov/scripts/scaffold.py render <worktree>`）
- Modify: `CHANGELOG.md`（架构图小节）
- Create: `docs/guide/plugins.md`
- Refresh (untracked): `docs/architecture-full-v2.html`、`docs/architecture-full.html`、`docs/architecture-simplify.html`（archify 工作流，与主检出同样保持未跟踪，不提交）

**Interfaces:**
- Consumes: Task 2–6 的模块列表（节点 files 字段必须真实存在）

步骤：
1. 复制 arch-gov 三件套后，在 `manifest.json` 加节点：
   `{"id": "plugins", "title": "plugins", "cluster": "base", "files": ["zenagent/plugins/__init__.py", "zenagent/plugins/manifest.py", "zenagent/plugins/state.py", "zenagent/plugins/discovery.py", "zenagent/plugins/install.py", "zenagent/plugins/catalog.py"], "resp": "声明式能力包：manifest 校验、发现、安装/卸载、启停与凭据、贡献解析", "symbols": ["PluginManifest", "ResolvedContributions"]}`
   并在 `edges` 加三条（`kind: "import"`）：`<app 装配节点> → plugins`（先查 app 簇里 files 含 `zenagent/app/runtime.py` 的节点 id）、`plugins → settings`、`plugins → project`（如 `manifest.py` 未 import project 则省略该边——**以代码实际 import 为准**）。
2. `scaffold.py render`，diff 确认只增不改其他节点；更新 `manifest.json` 的 `updated` 与 `overview` 计数（scaffold 生成 brief 会自动带计数）。
3. `CHANGELOG.md`：按既有格式加「架构图」小节，写明新增 `plugins` 节点、边方向、`data-node="plugins"` 引用。
4. `docs/guide/plugins.md`：概念（plugin/connector 三层模型）、安装/启停/卸载、三家连接器配置步骤（钉钉广场 URL、飞书 App ID/Secret + Node 前提、企微机器人 URL + 7 天到期）、凭据存储位置与不回显承诺、排障（401/超时/URL 失效）。
5. 用 archify 技能重生成三张 HTML（主检出副本，同为未跟踪产物，不入 git——若用户要求入库再单独决定）。

- [ ] **Step 1: 复制 arch-gov 资产并核对 app 节点 id**

Run: `cp -R /Users/mac/zen-agent/docs/arch-gov /Users/mac/zen-agent/.worktrees/plugin-system/docs/`，再用 `python3 -c "import json; m=json.load(open('.../manifest.json')); print([n['id'] for n in m['nodes'] if 'zenagent/app/runtime.py' in n.get('files',[])])"` 打印 app 装配节点 id。

- [ ] **Step 2: 改 manifest.json 并 render**

Run: `python3 /Users/mac/.agents/skills/arch-gov/scripts/scaffold.py render /Users/mac/zen-agent/.worktrees/plugin-system`
Expected: brief.md 头部计数 41 节点 / 190 边（按实际 diff），无其它节点变化。

- [ ] **Step 3: 写 CHANGELOG 架构节与 `docs/guide/plugins.md`**

- [ ] **Step 4: 全量门禁**

Run: `scripts/check.sh && cd desktop && pnpm test && pnpm build`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add docs/arch-gov docs/guide/plugins.md CHANGELOG.md
git commit -m "docs: plugin system architecture and user guide"
```

---

## 收尾检查（Task 10 后）

- [ ] `scripts/check.sh` 全绿（uv sync / ruff / 架构预算 / pytest）
- [ ] `cd desktop && pnpm test && pnpm build` 全绿
- [ ] allowlist 未放宽：`runtime.py`、`server.py`、`SettingsPage.tsx`、`messages.ts`、`appStore.ts`、`ops_config.py` 行数 ≤ 现值（理想为下降）
- [ ] `tests/test_rpc_contract.py` 通过（`plugins` 双向一致）
- [ ] 全仓 grep 确认没有凭据明文出现在 manifest/测试快照/文档：`grep -rn "plugin:" zenagent/plugins/data | grep -v "plugin:<id>"` 应为空
- [ ] 手工验收清单（不进 CI，交用户）：真实钉钉/飞书/企微各跑一次最小流程
