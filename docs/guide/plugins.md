# 插件（Plugins）

插件是 zen-agent 的**声明式能力包**：一个目录 + 一份 `manifest.json`，把外部应用接成模型可用的工具。
第一批官方插件是三个办公 IM 连接器——[钉钉](#钉钉)、[飞书](#飞书)、[企业微信](#企业微信)。

## 三层模型：plugin / connector / MCP server

| 层 | 是什么 | 例子 |
|---|---|---|
| **plugin** | 安装与治理单位：manifest（id、版本、凭据声明、贡献）+ 可选 skills / 扩展文件 | `zen.feishu` |
| **connector** | 产品化的一等能力：一个插件把某个外部应用接进来 | 消息、文档、日历、待办 |
| **MCP server** | 真正干活的进程或远端端点，由 zen 按 manifest 启动 / 连接 | `npx @larksuiteoapi/lark-mcp`、钉钉 MCP 服务 URL |

**调用方向是单向的**：zen → 插件贡献的 MCP server → 外部应用。插件进程拿不到 zen 的宿主 API，
也不会回调 zen；`zenagent/plugins/` 不 import `mcp` / `app` / `cli` / `agent` / `tools` / `providers`，
只有 `zenagent/app/plugin_wiring.py` 把插件贡献翻译成 MCP 配置（`tests/test_plugin_layering.py` 钉住）。

插件贡献的 MCP server 命名为 `<plugin-id>.<server-key>`（如 `zen.feishu.feishu`），
工具照常暴露为 `mcp__<server>__<tool>`。插件也可以贡献 skills（并入技能根）和一个 Python 扩展文件。

## 安装、启停、卸载

桌面端 **Settings → Plugins**：

- **官方连接器**：三张卡片，点「安装」从内置目录装入（安装的是包内同一份 manifest，走同样的严格校验）。
- **本地插件**：文本框填目录或 zip 路径，点「从路径安装」；zip 有路径穿越校验与大小上限。
- **启停**：每行一个开关。默认启用。写入 `settings.json` 的 `prefs.plugins.<id>.enabled`。
- **配置**：`needs_credentials`（「待配置」）时点「配置」，按 manifest 的 `credentials` 生成表单。
  安装后若该插件需要凭据，配置抽屉会自动打开。
- **卸载**：两段式确认，且明示会删除该插件的全部凭据与启停状态。

启停和配置立即重建 MCP 连接；如果当前回合正在流式输出，改动会**在本轮对话结束后生效**，
面板顶部显示「本轮对话结束后生效」提示条。

其他前端走同一个 RPC 操作：`{"type": "plugins", "op": "list" | "install" | "uninstall" | "enable" | "disable" | "configure", "plugin": "<id>", ...}`。
插件 id 放在 `plugin` 字段（`id` 保留给 RPC 请求关联）。

已安装插件在 `~/.zenagent/plugins/installed/<id>/`。发现阶段是**防御式**的：手工改坏的 manifest
只产生一条警告并跳过该插件，不会拖垮整个列表。

## 三家连接器

| 连接器 | 凭据 | 前提 | 备注 |
|---|---|---|---|
| 钉钉 | 整条 MCP 服务 URL | 钉钉 MCP 广场生成 | URL 即长效凭据 |
| 飞书 | App ID + App Secret | 本机 Node.js 18+（`npx`） | 官方 lark-mcp，工具标注 Beta |
| 企业微信 | 整条 MCP 服务 URL | 管理后台创建智能机器人 | 文档类授权 **7 天到期** |

### 钉钉

1. 用管理员账号打开 [钉钉 MCP 广场](https://mcp.dingtalk.com)，为组织生成一个 MCP 服务，按需勾选消息 / 日程 / 文档 / 待办。
2. 复制生成的**整条服务 URL**。
3. Settings → Plugins → 钉钉 → 配置，粘贴到「MCP 服务 URL」并保存。

### 飞书

1. 在[飞书开放平台](https://open.feishu.cn/app)创建「企业自建应用」。
2. 从「凭证与基础信息」复制 **App ID** 与 **App Secret**。
3. 在「权限管理」开通 IM / 文档 / 日历权限，发布版本并等管理员审批。
4. 本机安装 Node.js 18+；连接器用 `npx -y @larksuiteoapi/lark-mcp` 启动。
5. Settings → Plugins → 飞书 → 配置，填 App ID / App Secret 并保存。

凭据通过**环境变量**传给子进程，不出现在命令行参数里（`ps` 看不到）。

### 企业微信

1. 企业微信管理后台 →「智能机器人」→ 创建机器人，逐项授权消息 / 文档 / 日程 / 会议。
2. 管理员扫码确认，复制页面给出的**整条 MCP 服务 URL**。
3. Settings → Plugins → 企业微信 → 配置，粘贴 URL 并保存。

文档类权限 **7 天到期**：到期后工具返回 401，回管理页重新授权、换新 URL 即可，不需要重装插件。

## 凭据存在哪、怎么显示

- 只存在本机 `~/.zenagent/settings.json`（权限 0600），键名 `plugin:<id>:<KEY>`。
- manifest、项目文件、环境变量都**不能**提供插件凭据；只有这个存储里的值参与解析。
- 日志、RPC 应答、界面**从不回显**凭据值：`secret` 字段一律掩码，错误信息在 MCP 源头脱敏
  （`zenagent/mcp/redact.py`），URL 中内嵌的 key 同样掩码。
- 卸载插件会删除它的全部 `plugin:<id>:*` 键。
- **整条 URL 本身就是凭据**：不要贴进项目文件、聊天记录或截图。

## 排障

| 现象 | 先查什么 |
|---|---|
| 状态「待配置」 | 有凭据没填：点「配置」补齐 manifest 声明的每一 `key` |
| 状态「连接失败」+ 401/403 | URL 过期或授权失效：钉钉回广场重生成；企微重新授权（7 天到期）；飞书检查应用是否发布并通过审批 |
| 超时 | 本机网络能否访问对应域名（`mcp.dingtalk.com`、`open.feishu.cn`、`work.weixin.qq.com`） |
| 飞书启动即失败 | `npx` 不存在或 Node < 18；先手动跑一次 `npx -y @larksuiteoapi/lark-mcp --help` |
| 安装路径被拒 | manifest 校验失败会指名出错字段；zip 拒绝绝对路径 / `..` 穿越 / 超限体积 |
| 改完没生效 | 看顶部「本轮对话结束后生效」提示：流式回合结束后自动应用 |

失败不会卸载插件：状态与失败摘要留在列表里，修好凭据后下一次应用即重连。

## 源码对照

- 定义与校验：`zenagent/plugins/manifest.py`、`zenagent/plugins/state.py`（凭据 / 启停 / 贡献解析）
- 发现与安装：`zenagent/plugins/discovery.py`、`zenagent/plugins/install.py`、`zenagent/plugins/catalog.py`
- 桥接与装配：`zenagent/app/plugin_wiring.py`（唯一把插件贡献交给 MCP manager 的地方）
- RPC：`zenagent/cli/rpc/ops_plugins.py`；桌面端 `desktop/src/ui/pages/PluginSettings.tsx`、`desktop/src/state/pluginApi.ts`
- 三个内置 manifest：`zenagent/plugins/data/zen.{dingtalk,feishu,wecom}/manifest.json`（同目录 `setup.md` 为面板内指引）
- 测试：`tests/test_plugin_manifest.py`、`tests/test_plugin_state.py`、`tests/test_plugin_install.py`、`tests/test_plugin_catalog.py`、`tests/test_plugin_wiring.py`、`tests/test_plugin_runtime.py`、`tests/test_plugin_layering.py`、`tests/test_rpc_plugins.py`
