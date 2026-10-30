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

## 4.5 查询语法（Google 式操作符）

模型习惯在 query 里写 `site:`、`after:`、引号短语，不管后端引擎认不认。所以链在**调用前解析一次**，再按引擎能力重排：

| 操作符 | 含义 | DeepSeek | Tavily | Brave | Bing / DDG |
|---|---|---|---|---|---|
| `"exact phrase"` | 精确短语 | ✅ | ✅ | ✅ | ✅ |
| `-term` / `NOT term` | 排除词 | ✅ | 丢弃 | ✅ | ✅ |
| `site:a.com` | 限定站点 | ✅ | → `include_domains` | ✅ | ✅（仅裸域名，带路径会被降级成关键词） |
| `-site:a.com` | 排除站点 | ✅ | → `exclude_domains` | ✅ | ✅ |
| `filetype:pdf` | 文件类型 | ✅ | 丢弃 | ✅ | ✅ |
| `intitle:word` | 标题包含 | 丢弃 | 丢弃 | 丢弃 | ✅ |
| `inurl:word` | URL 包含 | 丢弃 | 丢弃 | 丢弃 | 降级成关键词（DDG 忽略 `inurl:`） |
| `lang:en` | 语言 | ✅ | 丢弃 | 丢弃 | 丢弃 |
| `after:2025-01-01` | 起始日期 | ✅ | → `days`（若同时给了 recency） | 丢弃 | 丢弃 |
| `before:2026-01-01` | 截止日期 | ✅ | 丢弃 | 丢弃 | 丢弃 |
| `a OR b` | 任一匹配 | ✅ | 丢弃 | ✅ | ✅ |

三条规则：

1. **能映射到 API 参数的就不用语法**：Tavily 的 `include_domains`/`exclude_domains` 由 `site:`/`-site:` 直接填充，query 文本里不再出现 `site:`。
2. **不认识的指令当普通关键词**：`issn:1234` 不是我们的指令，原样进 query；`after:yesterday` 日期解析失败也原样进 query。宁可搜不到也不吞掉用户输入。
3. **过严的约束自动放宽**：返回结果按 `site` / `filetype` / `intitle` / 日期做一次宽松过滤；**某个维度会清空整个结果集时，该维度被丢弃并在 `attempts` 里记一条 `relaxed site:x`**，而不是返回空。这条是 omp 的原设计，避免"加了一个过滤词就什么都搜不到"。

解析器在 `zenagent/tools/web/query.py`：`parse_query` → `StructuredQuery`，`format_query(query, syntax)` 按 `QuerySyntax` 重新拼，`format_scraper_query` 额外把"抓取式引擎不支持的 `site:` 带路径 / `inurl:`"降级成关键词，`apply_constraints` 做宽松过滤。

## 5. 结果规范化（只做压空白，不清洗内容）

不同引擎回来的东西差别很大，统一在 provider 里做三件事：

- **URL 去 fragment**：DeepSeek 会带 `#1`、`#:~:text=` 之类的锚点；`normalize_url` 再去掉追踪参数；
- **空白折叠 + 长度上限**：多行摘要折成一行（`\\s+` → 空格、trim），上限 400 字符；
- **空字段丢弃**：没有 title 或没有 url 的条目直接跳过（Bing 的导航块就会这样）。

**刻意不做的清洗**：不剥 markdown 标记（`###`、`>`）、不删"导航"类文字、不重写正文。

理由有两条：

1. **omp 就是这么做的**：`providers/utils.ts::normalizeSearchText` 只有 `replace(/\\s+/g, " ").trim()`，没有内容改写；截断发生在 TUI 渲染层（`clipCollapsed(..., 110)`），是"显示裁剪"而不是"数据清洗"。我们照抄这个分层：provider 只保证"一行、有上限"，展示层负责好看。
2. **剥标记会吃掉内容**：`^[#>]` 这类规则会把代码片段里的 `# comment` 一起删掉——早期版本这么干过，实测会把 Python 注释吞掉。搜索摘要是给模型读的，改写得越多，模型看到的越不是原文。

UI 上的单行效果由工具卡自己保证（卡片按行缩进渲染，超长自动折行/截断），不需要 provider 预先"美化"。

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

- `zenagent/tools/web/query.py` — `parse_query` / `format_query` / `format_scraper_query` / `apply_constraints`
- `zenagent/tools/web/registry.py` — `SearchChain.search` / `dedupe` / `configured_order`
- `zenagent/tools/web/providers.py` — `deepseek` / `tavily` / `brave` / `bing` / `duckduckgo`，`parse_bing` / `parse_duckduckgo`
- `zenagent/tools/web/tools.py` — `WEB_SEARCH` / `FETCH` / `format_results` / `run_search`
- `zenagent/app/commands.py` — `/search` 命令
- `tests/test_tools_web.py` — provider 与链的测试
