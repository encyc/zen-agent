# Web 搜索（多 provider 设计）

`web_search` 是 agent 唯一需要外网的读工具。它被设计成**一条 provider 链**，而不是绑死某个搜索引擎：不同的网络环境能连通的引擎完全不同，绑死一家等于在别的网络里没有搜索。

## 1. 结构

```
zenagent/tools/web/
├── net.py          # 唯一的网络接缝：get() / post()，测试只 patch 这两个
├── types.py        # SearchResult / SearchRequest / ProviderError / normalize_url
├── providers.py    # 5 个 provider + 各自的解析与清洗
├── registry.py     # provider 顺序、可用性、缓存、去重、失败汇总（SearchChain）
├── tools.py        # 模型看到的 web_search / fetch 两个 AgentTool
└── __init__.py     # 导出
```

工具层（`tools.py`）只做参数校验和格式化；provider 层只做"一个请求→`SearchResult[]`"；`SearchChain` 负责顺序、失败继续、缓存和去重。**加一个 provider 只需要在 `providers.py` 里加一个函数并登记**，其它层不用动。

## 2. Provider 清单

| provider | 凭证 | 端点 | 结果质量 | 时效过滤 | 本机可达 |
|---|---|---|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | `POST https://api.deepseek.com/v1/tools/search` | 好：真实 URL + 发布日期 + 长摘要 | `after:YYYY-MM-DD` 拼进 query | ✅ |
| `tavily` | `TAVILY_API_KEY` | `POST https://api.tavily.com/search` | 好（AI 检索专用） | `days` 参数 | 未验证 |
| `brave` | `BRAVE_API_KEY` | `GET https://api.search.brave.com/res/v1/web/search` | 好 | `freshness=pd/pw/pm/py` | 未验证 |
| `bing` | 无 | `GET https://cn.bing.com/search` | 一般：标题/摘要要额外清洗，排序常给首页 | 不支持 | ✅ |
| `duckduckgo` | 无 | `POST https://html.duckduckgo.com/html/` | 一般 | 不支持 | ❌（被墙） |

**默认顺序**：`deepseek → tavily → brave → bing → duckduckgo`。
有 key 的优先（质量高），无 key 的两个兜底（保证裸机器可用）。

## 3. 配置

| 环境变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY` | 同时给模型和搜索用（同一个 key） |
| `TAVILY_API_KEY` / `BRAVE_API_KEY` | 可选，有就进链 |
| `ZENAGENT_SEARCH_PROVIDERS` | 覆盖顺序，逗号分隔，例如 `deepseek,bing` |
| `ZENAGENT_SEARCH_TIMEOUT` | 单次请求超时秒数（默认 20） |

不写配置文件：搜索是"环境能力"，跟着 key 走比跟着仓库配置走更符合直觉（换机器不用改 repo）。

## 4. 链的行为

1. 按顺序试，**第一个返回非空结果的 provider 胜出**；
2. 每个 provider 的失败都记进 `attempts`，全失败时原样报给模型：
   `web search failed — DeepSeek: HTTP 401; Bing: no results; DuckDuckGo: connect timeout`；
3. 结果**去重**：按 URL 规范化（去掉 fragment、`utm_*` 等追踪参数、域名小写）后合并；
4. **缓存 60 秒 / 32 条**（key = 顺序 + query + limit + recency）。同一轮里模型重复搜同一句不会重复打 API；
5. `recency` 只对支持的 provider 生效（deepseek/tavily/brave），其它忽略而不是报错。

## 5. 结果规范化

不同引擎回来的东西差别很大，统一在 provider 里做三件事：

- **URL 去 fragment**：DeepSeek 会带 `#1`、`#:~:text=` 之类的锚点；`normalize_url` 再去掉追踪参数；
- **摘要压成一行**：DeepSeek 的 `summary` 是多行 markdown（`# 标题`、代码、`...`），先剥 markdown 标记再按行拼接，上限 260 字符——工具卡是单行，多行会把列表撑乱；
- **空字段丢弃**：没有 title 或没有 url 的条目直接跳过（Bing 的导航块就会这样）。

## 6. `fetch`

搜索只给摘要，读全文要 `fetch`：

- 只允许 `http(s)`；
- HTML 走 `strip_html`（剥 `<script>/<style>/<noscript>` 再剥标签）；
- 上限 12k 字符，截断时在文末标 `… (truncated)`，`details.truncated=true`；
- 返回头一行带状态码与字节数，方便判断是不是被反爬挡了。

## 7. 可观测性

- `/search`（无参数）打印 provider 状态：每个 provider 一行 `✓/✗ + 缺什么`，外加覆盖顺序的提示；
- `/search <query>` 直接跑一次链并把结果打到转录区，用来判断"换 provider 到底有没有变好"；
- `web_search` 的 `details` 里带 `provider` / `attempts` / `cached`，UI 与日志都能拿到。

## 8. 实测结论（本机，2026-10）

| 场景 | 结果 |
|---|---|
| `deepseek` + 真 key | ✅ 返回真实 URL、发布日期、可用摘要；"python 3.14 release date" 命中 PEP 745 与 python.org 版本页 |
| `bing`（keyless） | ⚠️ 能出结果但排序差（问版本号给首页），标题曾是面包屑、需专门清洗 |
| `duckduckgo` | ❌ connect 失败（网络不可达） |
| `tavily` / `brave` | 未测（无 key） |

结论：**有 DeepSeek key 时优先 DeepSeek**，它的 `v1/tools/search` 用同一个 `DEEPSEEK_API_KEY`，不需要另配服务——这也是默认链第一个的原因。

## 9. 测试

- 所有网络调用都经过 `net.get` / `net.post`，测试 patch 这两个函数即可，不联网；
- 每个 provider 有解析测试（含真实 HTML 片段：Bing 面包屑、DDG 重定向、DeepSeek markdown 摘要）；
- 链的行为单独测：fallthrough、全失败汇总、缓存命中、去重；
- `tests/test_tools_web.py` 覆盖以上全部。

## 源码对照

- `zenagent/tools/web/registry.py` — `SearchChain.search` / `dedupe` / `configured_order`
- `zenagent/tools/web/providers.py` — `deepseek` / `tavily` / `brave` / `bing` / `duckduckgo`，`parse_bing` / `parse_duckduckgo`
- `zenagent/tools/web/tools.py` — `WEB_SEARCH` / `FETCH` / `format_results` / `run_search`
- `zenagent/app/commands.py` — `/search` 命令
- `tests/test_tools_web.py` — provider 与链的测试
