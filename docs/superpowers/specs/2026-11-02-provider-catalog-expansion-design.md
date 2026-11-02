# Provider 目录扩展：对齐 pi-ai 主流厂商（8+1 家）

日期：2026-11-02
状态：待批准

## 意图与背景

zenagent 的模型目录 `zenagent/catalog.yaml` 目前只有 3 个 provider
（zai-coding-cn、deepseek、opencode-go）。目标是对齐 pi-ai
（`@mariozechner/pi-ai` 0.73.1，earendil-works/pi 的 packages/ai）的模型目录，
把我们点名的 8 家主流厂商补齐，外加 xiaomi（MiMo），使国内主流厂商阵营完整：

OpenAI、Z.AI、DeepSeek、Kimi/Moonshot、MiniMax、Google Gemini、xAI (Grok)、
OpenRouter、Xiaomi (MiMo)。

**为什么可行**：调研确认 pi-ai 42 个 provider 走约 10 种 wire 协议，我们点名的
9 家全部落在我们已有 4 个 adapter（openai-completions / openai-responses /
anthropic-messages / google-generative-ai）的范围内。**本工作不写任何新
adapter**，是纯数据（catalog 条目）+ 一次性转换脚本的工作。

## 目标

1. `catalog.yaml` 新增 9 家厂商的 provider + 模型条目（模型清单、价格、上下文
   窗口、compat 标志），数据照抄 pi-ai 0.73.1 的生成式目录
   （`dist/models.generated.js`）。
2. 每家"海外站 + 国内站"两个端点都录（用户按可达性选）。
3. 鉴权只支持 API key（`api_key_env`），不做 OAuth 订阅登录。
4. 转换脚本只跑一次，**不留仓库**（用户决定）；后续更新走手工。

## 非目标 / 反目标

- 不做订阅制 OAuth（kimi-coding、ChatGPT 登录、SuperGrok、OpenRouter OAuth
  在 pi-ai 里有，我们不抄这部分）。
- 不加新 wire 协议 adapter；不引 pi-ai 独有协议（bedrock、mistral、
  azure-responses、pi-messages 等）。
- 不收录 qwen-token-plan、ant-ling（本轮不做；pi-ai 里剩余国内源只有这两个）。
- 不改变 catalog.yaml 语法或 loader（catalog.py）能力——现有语法已够用。
- 反目标：覆盖现有手工调优过的条目（zai-coding-cn、deepseek、opencode-go），
  或破坏现有测试。

## 数据来源与覆盖规则

- 锁定数据源：npm 包 `@mariozechner/pi-ai@0.73.1` 的
  `package/dist/models.generated.js`（553KB，969 个模型条目，字段：
  id/name/api/provider/baseUrl/compat/reasoning/input/cost/contextWindow/
  maxTokens/thinkingLevelMap）。
- **手工条目保留**：现有 `zai-coding-cn`、`deepseek`、`opencode-go` 三个
  provider 的条目是手工核对过的（含注释、价格口径、compat 调优），生成时
  **跳过这三个 provider，不覆盖**。
- 新 provider 的 env 变量名对齐 pi-ai 的 `envApiKeyAuth` 命名
  （OPENAI_API_KEY、ZAI_API_KEY、DEEPSEEK_API_KEY、KIMI_API_KEY、
  MINIMAX_API_KEY、XIAOMI_API_KEY、GOOGLE_API_KEY、XAI_API_KEY、
  OPENROUTER_API_KEY 等；与 pi-ai 不一致处以 pi-ai 为准）。

### 厂商与端点清单（"两家都录"）

**协议归属已用 node 导入 pi-ai 0.73.1 的 `models.generated.js` 逐条核实**
（每个 provider 内 api 唯一，无混用，因此 adapter 写在 provider 级即可）：

| 我们 provider id | pi-ai provider | 协议(adapter) | base_url | 模型数 |
|---|---|---|---|---|
| openai | openai | openai-responses | api.openai.com/v1 | 42 |
| zai | zai | openai-completions | api.z.ai/api/coding/paas/v4 | 5 |
| zai-coding-cn（已有，保留） | zai-coding-cn | openai-completions | open.bigmodel.cn/api/coding/paas/v4 | (已有 2) |
| deepseek（已有，保留） | deepseek | openai-completions | api.deepseek.com | (已有 2) |
| kimi | kimi-coding | **anthropic-messages** | api.kimi.com/coding | 2 |
| moonshotai | moonshotai | openai-completions | api.moonshot.ai/v1 | 7 |
| moonshotai-cn | moonshotai-cn | openai-completions | api.moonshot.cn/v1 | 7 |
| minimax | minimax | anthropic-messages | api.minimax.io/anthropic | 2 |
| minimax-cn | minimax-cn | anthropic-messages | api.minimaxi.com/anthropic | 2 |
| google | google | google-generative-ai | generativelanguage.googleapis.com/v1beta | 27 |
| xai | xai | **openai-completions** | api.x.ai/v1 | 25 |
| openrouter | openrouter | openai-completions | openrouter.ai/api/v1 | 精选（见下） |
| xiaomi | xiaomi | **anthropic-messages** | api.xiaomimimo.com/anthropic | 5 |
| xiaomi-token-plan-cn | xiaomi-token-plan-cn | anthropic-messages | token-plan-cn.xiaomimimo.com/anthropic | 5 |
| xiaomi-token-plan-ams | xiaomi-token-plan-ams | anthropic-messages | token-plan-ams.xiaomimimo.com/anthropic | 5 |
| xiaomi-token-plan-sgp | xiaomi-token-plan-sgp | anthropic-messages | token-plan-sgp.xiaomimimo.com/anthropic | 5 |

**三条与直觉相反、已核实的协议事实**（最初按 provider 名猜错了，已改）：

1. `kimi-coding` 与 `xiaomi`（含 4 个端点）走 **anthropic-messages**，不是
   openai-completions。base_url 不带 `/v1`（我们的 anthropic adapter 会补
   `/v1/messages`，`anthropic_messages.py:252`）。
2. `xai` 走 **openai-completions**，不是 openai-responses（pi-ai 的 provider
   工厂上写 responses，但条目级 api 是 completions——以条目为准）。
3. `openai` 走 openai-responses（与直觉一致）。

新增模型数合计：42+5+2+7+7+2+2+27+25+5×4 = **139**（不含 openrouter 精选 82 条）。
现有目录为 3 个 provider / 8 个模型（`zai-coding-cn` 2、`deepseek` 2、`opencode-go` 4）；
`deepseek` 已存在，**不新增**，只在表中标为"已有、不动"。全部完成后
**17 个 provider / 229 个模型**。

注：moonshotai（海外）与 kimi（kimi-coding 订阅端点）是不同端点，分开录。


## OpenRouter 精选规则

全量 275-499 条不可取（catalog.yaml 会膨胀到 1.5 万行）。精选规则：

1. 模型 id 按 `/` 前缀过滤，只保留与我们已录厂商对应的 vendor 前缀白名单：
   `anthropic/`、`openai/`、`google/`、`x-ai/`、`deepseek/`、`z-ai/`、
   `moonshotai/`、`minimax/`、`xiaomi/`。
2. **排除含 `:` 的 id**（`:thinking`/`:free`/`:latest` 等变体后缀）：catalog
   解析器对每行 `partition(":")`，带冒号的 key 会被切坏，语法表达不了。
   base id 已覆盖同一模型，变体不损失实际可用性。（builder check 发现，
   已核对 catalog.py `_tokenize`。）
3. 每个 vendor 只保留**当前在售的最新一代**，剔除明确的旧代与 preview
   快照。白名单初筛 138 条，排除 7 条带冒号变体后 131 条，再按"最新一代"
   精选到 60-90 条。
3. compat：openrouter 网关会归一化上游方言，compat 留空（opencode-go 先例），
   但 thinking 模型保留上游 thinkingFormat 映射（见下表）。

目标体量：catalog.yaml 总计约 150-200 个模型、2000-3000 行。

## 字段与 compat 映射（pi-ai → 我们）

模型级字段：

| pi-ai | catalog.yaml | 说明 |
|---|---|---|
| id | model key | 原样 |
| api | adapter（provider 级）| 值域完全一致 |
| baseUrl | base_url（provider 级）| 同一 provider 内一致的提到 provider 级 |
| contextWindow / maxTokens | context_window / max_tokens | |
| reasoning | reasoning | |
| input 含 "image" | supports_vision: true | 其余省略 |
| cost.input/output/cacheRead/cacheWrite | cost: {input:…, output:…, cache_read:…, cache_write:…} | loader 支持 cache_write（builder check 核实 `_cost_from`），照抄 |
| name / thinkingLevelMap / supportsStore 等 | 丢弃 | 我们无对应概念 |

compat 块（camelCase → snake_case，其余 pi-ai 独有键丢弃）：

| pi-ai | ours |
|---|---|
| thinkingFormat: "zai" | thinking_format: zai |
| thinkingFormat: "deepseek" | thinking_format: deepseek |
| thinkingFormat: "qwen" | **暂无对应** —— 涉及条目转为 compat 留空 + YAML 注释标
注"qwen thinking 未适配"，或忽略该 compat（本轮不实现 qwen 方言） |
| maxTokensField: "max_tokens" | max_tokens_field: max_tokens（与默认一致，可省）|
| supportsDeveloperRole: false | supports_developer_role: false |
| supportsReasoningEffort: false | supports_reasoning_effort: false |
| zaiToolStream: true | zai_tool_stream: true |
| requiresReasoningContentOnAssistantMessages: true | requires_reasoning_content_on_assistant: true |
| supportsStrictMode / sendSessionAffinityHeaders / supportsEagerToolInputStreaming | 丢弃（我们无对应机制）|

注意事项：
- pi-ai 的 `supportsReasoningEffort: false` 表示"不支持"，我们的默认值就是
  false —— 只有显式 `true` 才写。转换脚本按"偏离默认才写"的原则生成紧凑条目。
- openai-responses 适配器对我们是已注册 adapter，openai/xai 条目直接用。
- 每个 provider 挑一个当前旗舰设 `default: true`；已核实 loader 不检查全局
  唯一性（先例：zai-coding-cn 的 glm-5.3），保持每 provider 一个即可。

## 实施方式（一次性脚本，不入仓库）

1. 下载 pi-ai 0.73.1 npm 包，用 `node -e 'import("./models.generated.js")'`
   动态导入并 `JSON.stringify(MODELS)` 导出（已验证：31 个 provider、字段
   完整可读，比正则可靠）。
2. 按"覆盖规则 + 端点清单 + 精选规则 + 映射表"生成新 provider 的 YAML 文本，
   手工拼回 `catalog.yaml`（保留现有 3 个 provider 与文件头注释原样）。
3. 生成的条目带一行来源注释（`# synced from @mariozechner/pi-ai@0.73.1,
   2026-11-02`），方便日后溯源与手工更新。
4. 脚本本身（Python 或 node 临时脚本）不留仓库。

**token plan 端点的 cost 口径**：xiaomi-token-plan-* 是包月套餐，照 pi-ai
目录原样抄（pi-ai 已把套餐端点计为 0），与 zai-coding-cn 包月计 0 的先例一致；
在 YAML 里加注释说明"包月套餐，按量计 0"。

## 验证

- `bash scripts/check.sh` 全绿（sync + ruff + pytest；catalog.py 的解析器会
  逐行校验新 YAML 语法：无列表/锚点/引号——生成器必须输出受限语法）。
- 新增一个目录完整性测试（放 tests/，与现有 catalog 测试同风格）：
  - 9 家新 provider 均可加载，模型条目数 ≥ 约定下限（如 openai ≥ 40、
    google ≥ 27、xai ≥ 25、minimax 2+2、kimi 2 等）；
  - 每个 api 值都在 registry 注册表中（openai-completions /
    openai-responses / anthropic-messages / google-generative-ai）；
  - 现有 3 个手工 provider 条目逐字节不变（防覆盖回归）。
- 不做真实网络调用（`./scripts/eval.sh` 花钱，可选，不属于本任务门禁）。

## 留给 builder 的裁量

- OpenRouter 精选中"最新一代"的具体取舍（按发布代际剔除旧款与 preview 快照，
  白名单 131 条 → 目标 60-90 条）。
- 解析 models.generated.js 的具体手段（已验证可行：`node -e` 动态
  `import()` 导出 `MODELS` 为 JSON，见下）。
- 每个 provider 的 `default: true` 选哪个模型（取该 provider 最新旗舰）。

## 决策记录（用户已拍板）

- 端点：海外站 + 国内站两家都录。
- 模型清单与价格：照抄 pi-ai 0.73.1 目录，不逐家官网核对。
- 鉴权：只支持 API key，不做订阅 OAuth。
- 追加 xiaomi（MiMo，4 端点）使国内厂商齐备。
- 转换脚本一次性使用，不留仓库。
- openrouter 精选（vendor 白名单 + 最新一代 + 排除冒号变体），不做全量镜像。

## Builder check 结论（2026-11-02，子代理中止后由 agent 自查）

核对对象：catalog.py 全文（_tokenize/_parse_value/_model_from/_cost_from/
_provider_from/find_model）。三处修正已合入上文：

1. openrouter 带冒号变体 id 无法通过 `_tokenize` 的 `partition(":")` ——
   精选规则加排除条款（原设计会让 40+ 条 openrouter 条目在加载时报错或
   静默变成错误 id）。
2. cost.cache_write 丢弃 → 照抄（loader 支持，原设计写错了）。
3. default 唯一性"待 builder 核对" → 已核对：无全局唯一性约束，每 provider
   一个，从裁量清单移入已决。
