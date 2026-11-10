# 插件系统设计（Plugin System）

日期：2026-11-10 · 状态：待审批
上游决策：用户已选定「格式按能力包（plugin）设计，首批内容按官方连接器（connector）交付」。

## 1. 意图与背景（why）

zen-agent 需要让普通用户以「安装 + 授权」的方式接入外部服务（首批：钉钉、飞书、企业微信三个办公 IM），而不是手改 `mcp.json`。同时确立一个可生长的**打包分发层**，未来第三方可贡献能力包。

行业调研结论（2026-11，各家官方文档）：概念已收敛为三层——

| 层 | 含义 | 代表 |
|---|---|---|
| 协议层 MCP | agent 调外部工具的标准协议 | stdio / Streamable HTTP server |
| 打包层 plugin/extension | 可安装能力包：skills/命令/hooks/MCP 配置 | Claude Code plugin、pi-desktop plugin、Gemini extension |
| 产品化层 connector | 预置集成 + 一键授权，面向非开发者 | Claude Desktop connector、Qoder connector |

zen-agent 的「插件」= 打包层单元；「连接器」= 首批插件内容（每个连接器是一个插件）。参考项目 pi-desktop 的插件是重运行时（独立进程 + API broker + 面板/后台服务），本设计**不采用**该形态（见 §4）。

## 2. 目标 / 非目标 / 反目标

**目标**
- 确立 plugin 概念与 manifest 格式：目录 + `manifest.json` 的声明式能力包。
- 插件生命周期：安装（内置目录 / 本地目录 / zip）、启用/停用、卸载、凭据配置。
- **单向调用原则（用户明确约束）**：zen → 插件的 MCP server 进程 → 外部 app。插件进程是 zen 监管的常驻后台进程，但不获得任何宿主 API，不反向调用 zen。引入插件的目的就是让 zen 操作钉钉/飞书等外部 app。
- 桌面端管理面板（GUI 优先，Settings 新增 Plugins 目的地）；RPC 面供所有前端复用。
- 首批三个官方连接器插件：zen.dingtalk / zen.feishu / zen.wecom，包装各厂商**官方 MCP 服务**，附使用引导 skill。
- 凭据走现有 `settings.json`（0600）secrets 机制，启动时注入，manifest 不存值。
- 启用/停用/配置变更**免重启生效**（装配层新路径，见 §7）。

**非目标（v1 不做）**
- 托管市场、签名、计费、自动更新。
- 插件自有代码的宿主运行时：brokered 宿主 API、自定义 UI 面板、进程沙箱。注意：插件**有**常驻后台进程（MCP server，McpManager 监管），排除的是插件反向调用 zen 的通道。
- IM 消息的被动接收/实时推送（与单向原则冲突；v1 由 agent 用工具主动读取）。
- 应用内 OAuth 浏览器流程（飞书 `--oauth`、企微扫码发生在厂商页面，用户粘贴结果）。
- 项目级插件作用域（v1 仅全局安装）。
- TUI/REPL 的插件管理界面（headless 场景直接管理文件即可）。

**反目标（做成这样即失败）**
- 改动 `agent/` 内核一行；引入新第三方依赖；产生循环 import。
- 凭据出现在 manifest、项目层文件、日志或 RPC 回显里。
- 一个坏插件拖垮会话（必须像 extensions 一样失败隔离）。
- 绕过现有门禁（layering 测试、800 行预算、RPC 契约测试、arch-gov 图治理）。

## 3. 约束（项目既有规则，逐条钉死）

1. 六层依赖只向下（`tests/test_layering.py`）；扩展面在 `agent/` 之上/旁边接线，不改 loop（`docs/architecture.md`）。
2. 核心依赖仅 httpx（`pyproject.toml`）；plugins 模块纯 stdlib（JSON manifest，不引 YAML）。
3. 单文件 ≤800 行（`scripts/check_architecture.py` + allowlist 只许降）。**顶格文件策略**：新逻辑全部进新文件（`ops_plugins.py` mixin、`PluginSettings.tsx`、`pluginStore.ts` 等）；必须触碰的顶格宿主（runtime.py / ops_config.py / SettingsPage.tsx / i18n messages）只留薄钩子，并通过抽出既有内容腾出空间——宿主行数只降不升。
4. RPC 契约双向钉住（`tests/test_rpc_contract.py` ↔ `desktop/src/rpc/types.ts` 的 `REQUEST_KINDS`）。
5. arch-gov：设计经图——新节点/边落回 `docs/arch-gov/manifest.json`（scaffold 生成 brief，勿手改），更新架构图资产，CHANGELOG 写架构图小节。
6. i18n：扁平 dict、英文目录为准（`zenagent/i18n`）。
7. secrets 只进 `~/.zenagent/settings.json`（0600，`zenagent/settings.py`），优先级高于环境变量。

## 4. 候选方案与取舍

**A. 声明式能力包，复用现有扩展面（选定）**
插件 = 目录 + manifest，声明 MCP server、skills 目录、可选 Python extension。发现/解析是纯数据模块，`app/runtime.py` 把贡献喂给现有 McpManager / discover_skills / load_extensions。
优点：零新依赖、内核零改动、无新协议、授权/启停全部落在现有机制；与 skills.py「发现即数据」同构。
代价：v1 插件没有自定义面板；插件不获得宿主 API（单向调用，用户明确约束）。插件代码执行仅复用既有 extension 通道（受信、opt-in）。

**B. pi-desktop 式 brokered 运行时（否决）**
独立插件进程 + JSON-RPC API broker + 沙箱面板。扩展力最强，但需要新建进程宿主、API 面与安全边界，和零依赖/极简内核理念冲突；且 brokered API 正是用户明确排除的「插件反向调 zen」通道。若未来需要，可在 manifest 上演进，不阻塞今天。

**C. 不做插件概念，只预置 mcp.json 模板（否决）**
最省，但没有安装/启停/授权 UX，也不建立用户明确要求的打包层；「装一个钉钉连接器」的体验不存在。

用户已确认方向：格式按能力包设计（A 的形态），首批内容为官方连接器。

## 5. 概念模型与目录布局

```
~/.zenagent/plugins/
├── installed/
│   └── zen.dingtalk/          # 一个插件 = 一个目录
│       ├── manifest.json      # 唯一必需文件
│       └── skills/            # 可选：引导 SKILL.md
│           └── dingtalk-guide/SKILL.md
└── (无 disabled/ 目录：启停是状态，不是搬目录)
```

- **plugin**：可安装能力包。id 全局唯一（正则 `^[a-z0-9][a-z0-9.-]*$`）。
- **connector**：以「预置 MCP server + 凭据声明 + 引导 skill」为主要贡献的插件。是内容分类，不是新机制。
- 与现有扩展面的关系：`mcp.json` 仍是用户手工通道，**不变**；插件的方法贡献由 runtime 合并。同名冲突时**用户手工配置优先**（用户 > 插件；项目层 > 全局层的既有规则不变）。
- 状态存储：启停标志进 `settings.json` 的 `prefs["plugins"]`（`{id: {"enabled": bool}}`）；凭据进 `api_keys`，键名命名空间化 `plugin:<id>:<key>`。不新建状态文件。

## 6. manifest.json 格式

```json
{
  "schemaVersion": 1,
  "id": "zen.feishu",
  "name": "飞书",
  "version": "0.1.0",
  "description": "飞书官方 MCP 连接器：IM、文档与日历",
  "contributes": {
    "mcpServers": {
      "feishu": {
        "command": ["npx", "-y", "@larksuiteoapi/lark-mcp", "-t", "preset.im.default,preset.doc.default,preset.calendar.default"],
        "env": {"APP_ID": "${APP_ID}", "APP_SECRET": "${APP_SECRET}"}
      }
    },
    "skills": "skills",
    "extension": null
  },
  "credentials": [
    {"key": "APP_ID", "label": "App ID", "secret": false,
     "help": "飞书开放平台 → 应用凭证", "url": "https://open.feishu.cn/app"},
    {"key": "APP_SECRET", "label": "App Secret", "secret": true}
  ],
  "setup": {
    "guide": "setup.md",
    "requiresRuntime": "Node.js 18+（npx）"
  }
}
```

规则：
- `contributes.mcpServers` 条目与 `mcp.json` 的 server 形状**同形**（`command` xor `url`，`env`，`headers`），复用 `zenagent/mcp/config.py` 的解析校验逻辑（经公开包装函数，不 import 私有符号）。
- 飞书官方 `lark-mcp` 的工具集用 `-t`（`-p` 是端口）；凭据走 `env`（`APP_ID`/`APP_SECRET`）而非 `-a/-s` 参数，避免凭据出现在子进程 argv（`ps` 可见）。
- `${KEY}` 占位符出现在 command/env/url/headers 中，合并时从 secrets 存储替换；**任一必需凭据缺失 → 该插件状态 `needs_credentials`，其 server 不启动**，不是错误，是待设置。
- `credentials[].secret=true` 的值永不回显（RPC/日志/UI 一律掩码）。URL 内嵌 key 的情形（钉钉/企微托管 URL）整个 URL 按 secret 处理。
- `contributes.skills` 是插件目录内的相对路径，发现时追加进 `discover_skills` 的 roots 末尾（用户 skill 同名优先）。路径必须落在插件目录内（防穿越）。
- `contributes.extension` 可选，指向插件内 Python 文件，走既有 `load_extensions`（受信代码，UI 明示风险）；**官方连接器不携带任何代码**。
- `setup.requiresRuntime` 由 UI 展示为运行前提提示（如 Node）；zen 核心不因此新增依赖。
- 校验时机：**安装时严格校验**（坏 manifest 立即拒绝，install 返回 `plugin_failed`）；发现时防御性复检（装后被手改坏 = 警告 + 跳过，失败隔离，同 extensions）。

## 7. 模块归属与装配接线

**新模块 `zenagent/plugins/`**（base 簇扩展面，与 `mcp/`、`skills.py` 并列，纯 stdlib）：

| 文件 | 职责 |
|---|---|
| `manifest.py` | manifest 解析/校验，`PluginManifest` dataclass |
| `discovery.py` | `discover_plugins(roots)` → (plugins, warnings)；失败隔离 |
| `install.py` | 本地目录/zip 安装、卸载；zip 成员路径校验（禁 `..`/绝对路径）+ 解压大小上限 |
| `catalog.py` | 内置官方目录：三个连接器的 manifest 模板 + setup/skill 文案（包内数据文件） |
| `state.py` | 启停/凭据读写（代理 settings.py）；`contributions(plugin)` 产出合并用的纯数据 |

依赖方向：`plugins/` 只 import base 模块（settings、project、frontmatter 如需）；**不 import `mcp/`、`app/`、`cli/`**（manifest 的 server 条目输出为原始 dict，由 runtime 交给 mcp.config 解析）。`app/` import `plugins/`，与既有 `app→mcp` 边同向。无环，layering 测试天然通过。

**装配（`app/runtime.py`，唯一装配入口的延伸）**：
1. `Runtime.bootstrap`：发现已安装且启用的插件 → 合并贡献：
   - MCP：插件 server 以 `<plugin-id>.<server-key>` 为 server 名注入（manifest 的 mcpServers 是 map，统一命名无特例；工具名如 `mcp__zen_feishu_feishu__<tool>`，既有 sanitize 规则处理点号）；凭据替换后与用户 mcp.json 合并，用户同名优先；缺凭据的跳过并记 warning。
   - skills：插件 skills 目录追加到 roots 末尾。
   - extension：路径追加到 extension_files。
2. **免重启生效**：现状 `refresh_mcp()` 只重连既有 server、不重读配置。新增装配层方法 `Runtime.reconfigure_plugins()`：重算合并配置 → 关闭旧 McpManager → 新建并 start → `_recompose()`；沿用 `is_streaming` 不变量：流式中**状态与凭据照常持久化**，热应用推迟到本轮结束，结果帧明示「本轮后生效」（与 skills reload 先例一致）。skills/extension 变更走既有 `rediscover()`。这是 app 层新方法，内核不动。
3. RPC 写操作（安装/启停/配置/卸载）成功后调用该路径，再回结果帧。
4. **多进程语义**：插件全局，但每个前端进程各持 Runtime 与 settings 缓存。操作所在进程热生效；其他进程下次启动/重载时生效。v1 不做文件监听/跨进程推送。

## 8. RPC 面（ConfigOps 旁新增 PluginsOps mixin）

沿用现有 `type + op` 风格（同 `mcp`）：`type: "plugins"`

| op | 载荷 | 结果 |
|---|---|---|
| `list` | — | 内置目录 + 已安装列表：id/name/version/enabled/status（`ready`/`needs_credentials`/`connected`/`failed`）/server 状态/凭据填充情况（布尔，不回显值） |
| `install` | `{id}`（内置）或 `{path}`（本地目录/zip） | 安装后 list 视图 |
| `uninstall` | `{id}` | 删目录 + 清状态与凭据 |
| `enable` / `disable` | `{id}` | 触发 reconfigure + list 视图 |
| `configure` | `{id, values: {key: value}}` | 写 secrets → reconfigure；值不回显 |

- 错误走既有 `failure_frame`（code `plugin_failed`，op 字段）。
- `tests/test_rpc_contract.py` 与 `desktop/src/rpc/types.ts` 同步更新，双向钉住。

## 9. 桌面端（GUI 优先）

- `SettingsPage.tsx`：resources 组新增 `plugins` 目的地（skills/mcp 旁），lazy chunk `PluginSettings.tsx`。
- 面板结构：
  - **官方连接器**区：三张卡片（钉钉/飞书/企微），简介 + 安装按钮 + 运行前提提示。
  - **已安装**列表：启停开关、状态徽标（待配置/已连接/失败 + 错误摘要）、配置抽屉（按 credentials 声明生成表单 + help 链接）、卸载（确认对话框，明示将删除凭据）。
  - **本地安装**入口：目录/zip 选择。
- 状态：`pluginStore`（zustand，模式同 `useMcpStore`）+ appStore RPC actions；i18n 词条 en 为准。
- 交互闭环：安装 → 自动打开配置抽屉 → 填凭据 → 连接状态可见 → 工具出现在会话。

## 10. 首批三个官方连接器（内置目录内容）

「官方」= zen 第一方出品、包装厂商官方 MCP 服务；不 vendoring 第三方代码。每个连接器附一个引导 skill（工具命名、常用流程、授权排障）。

### zen.dingtalk（钉钉）
- **主形态：托管 MCP 广场**（mcp.dingtalk.com）。管理员在广场生成远程 Streamable HTTP URL（`mcp-gw.dingtalk.com/server/…?key=…`），用户粘贴 URL 完成配置。凭据 = 整个 URL（secret，key 内嵌）。覆盖文档、AI 表格、日历、群聊等。
- 备选（文档说明，不进 v1 目录）：本地 npm `dingtalk-mcp`（stdio，Client ID/Secret + `ACTIVE_PROFILES` 选服务域；不含文档/审批）。
- setup 文案：广场生成步骤 + URL 属长效凭证的保管提示。

### zen.feishu（飞书）
- `npx -y @larksuiteoapi/lark-mcp`（stdio），应用身份 app_id/app_secret；**preset 限定工具集**（官方 600+ 工具全量会撑爆上下文；具体 preset 清单实现时按 IM+文档场景挑选并写入 manifest）。
- 运行前提 Node.js 18+，UI 明示。厂商仍标 Beta，setup 文案注明接口可能变动。
- OAuth user-token 模式与远程 mcp.feishu.cn 变体：非目标，文档提及。

### zen.wecom（企业微信）
- API 模式「智能机器人」的托管 MCP URL（Streamable HTTP）。用户在企微管理页创建机器人、逐项授权、扫码后复制 URL 粘贴。凭据 = URL（secret）。
- **7 天到期问题**（文档类授权）：v1 不做计时器；连接失败时错误提示引导重新授权，setup 文案置顶说明。
- 社区 webhook 类 server 仅能发消息，不采用。

## 11. 安全

- 凭据仅存 settings.json（0600）；manifest/项目文件/日志/RPC 回显零出现；secret 字段与内嵌 key 的 URL 一律掩码。
- **脱敏落点在 mcp/ 错误源头**：HttpTransport/McpManager 构造错误文本时对 URL 查询参数与 authorization 头掩码。现有链路会把 httpx 异常原文（含完整 URL）带进 warnings/RPC/会话记录，只在展示层脱敏堵不住。此改动在扩展面 `mcp/`，内核仍零改动；顺带覆盖用户自配的远程 server。补脱敏单测。
- zip 安装：成员路径校验 + 解压大小上限 + id 校验；安装目录不越界。
- 官方连接器纯声明式、零代码；第三方 `extension` 通道沿用既有 opt-in 受信模型并明示风险。
- 插件失败隔离：解析/连接失败 = 警告 + 跳过，不影响会话与其他插件。

## 12. 测试策略

- 单测：manifest 校验（含占位符/路径穿越/坏 JSON）、发现隔离、安装/卸载、凭据替换与 needs_credentials、合并优先级（用户 > 插件）、reconfigure 的流式拒绝。
- 契约：RPC 双向契约测试更新；layering 门禁通过；800 行预算内。
- 桌面 e2e：安装 → 配置 → 启用 → 会话可见工具（ mock MCP server，不依赖真实厂商）。
- 真实厂商联通性：手工验收清单（三家各跑一次最小消息/文档流程），不进 CI。

## 13. 治理交付（与代码同批）

- arch-gov：manifest.json 新增 `plugins` 节点（base 簇）+ 边 `app.assembly→plugins`、`plugins→settings`、`plugins→project`；scaffold 重新生成 brief/nodes/deps；更新三张架构图资产。
- CHANGELOG：架构图小节（增节点/边，引用 data-node id）。
- 用户文档：`docs/guide/plugins.md`（概念、安装、三家连接器配置步骤）。

## 14. 留给实现者的细节

- manifest 各字段校验的具体错误文案；lark-mcp preset 的具体工具清单；配置抽屉与卡片的具体样式（沿用现有 Settings tile 模式）；测试 fixture 组织；内置目录数据文件的打包方式（importlib.resources 或等价 stdlib 方案）。

## 附：调研来源

- 钉钉：open.dingtalk.com MCP 概述 / github.com/open-dingtalk/dingtalk-mcp / mcp.dingtalk.com 广场
- 飞书：github.com/larksuite/lark-openapi-mcp / npm @larksuiteoapi/lark-mcp / open.feishu.cn 远程 MCP 文档
- 企微：open.work.weixin.qq.com 智能机器人文档（CLI+MCP 开放公告）/ github.com/WecomTeam/wecom-cli
- 行业对比：code.claude.com/docs/en/plugins / support.claude.com connectors / docs.qoder.com connectors / pi-desktop docs/spec/07-plugins