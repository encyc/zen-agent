# README 改为落地页（设计）

2026-11-02 · 目标文件：`README.md`（377 行 → ≤130 行）+ 3 个 docs 页面增补

## 意图与为什么

现在的 `README.md` 是参考手册：14 个章节里 **130 行是速查表格**（45 条斜杠命令、13 个工具、完整
`config.toml`、6 条架构契约、`## Status` 里程碑清单），访客要滑过 137 行才看到代码契约；而它同时是
**PyPI 项目页正文**（`pyproject.toml::readme`），第一屏决定陌生人装不装。

已经确认的定位变化：这个项目不再以"作者亲手实现一遍"作为卖点（教学叙事作废）。现在的卖点是
**小而快、纯 Python、四接入面、可嵌入、无厂商锁定、成本可见**。

## 目标

- README 压到 ≤130 行，严格四段：下载 → 是什么/为什么 → 特性 → 贡献。
- 第一段让陌生人在 30 秒内跑起来（含 API key）。
- 第二段用 F2（生态里缺的小内核）当主线，可量化的事实优先（451 KB wheel、1 个依赖、0.1 s 启动）。
- 参考级内容进 `docs/`，README 只留一句链接；不重复维护同一份内容。

## 非目标

- 不做中文 README（已确认只有英文）。
- 不改 `docs/` 的中文语言与三层结构（guide / internals / reference）。
- 不改任何代码行为，只动文档。

## 反目标（做出来算失败）

- README 又长回 200 行以上（说明参考内容没搬干净）。
- 出现找不到出处的量化断言（任何数字都要能指到 `dist/`、`uv.lock`、`pyproject.toml` 或一次实测）。
- 同一份内容在 README 和 docs 两处各写一遍（漂移的源头）。

## 约束

- **PyPI 会把 README 原样渲染成项目页**，相对链接在那里会失效 → 所有跨文件链接用绝对地址
  `https://github.com/encyc/zen-agent/blob/main/...`（现在的 README 有 3 处相对链接，是坏的）。
- 读者 Python 要求是 **3.11+**（`requires-python = ">=3.11"`）；`.python-version` 是 3.15，那只是
  uv 开发工具链的 pin。现在 README 写 `Requires Python 3.15+` 并把这句话传播到了 PyPI —— 是错的。
- 用户已裁掉的说法：**第二 part 不提教学/学习**；特性表**不收**"契约优先、离线测试、护栏、会话即文件"
  （判定为现在的 harness 普遍具备，不构成差异点）。

## 走过的路（选定 A）

| 方案 | 内容 | 结论 |
|---|---|---|
| **A 纯落地页** | README ≤130 行；参考内容全部迁 docs，缺的 3 处补齐 | **已选** |
| B 落地页 + `<details>` 折叠附录 | ~200 行，命令表/工具清单折叠保留 | 否：还是手册，且双处维护必然漂移 |
| C 落地页 + 双语 README | 英文 + `README.zh-CN.md` | 否：用户明确不要中文 README |

## 内容搬迁清单

| README 现状 | 去向 | 动作 |
|---|---|---|
| 45 条斜杠命令表（138–182） | **新建 `docs/guide/commands.md`** | 搬走 + README 留一句链接 |
| `## Tests` + `## Releasing`（310–357） | **新建 `docs/development.md`** | 搬走（含 `release-check.sh` 细节、publish dry-run） |
| `[task]` 子代理预算段（212–247 里的一段） | `docs/guide/config.md` | **补缺**（现在 docs 里完全没有） |
| `## Tools` 段落（287–309） | `docs/guide/tools-permissions.md` | 已覆盖，删除 README 版本 |
| `### Contracts worth knowing`（264–286） | `docs/reference/contracts.md` | 已覆盖（doc 有 10 条），删除 |
| `## Configuration` 全字段（183–247） | `docs/guide/config.md` | 已覆盖，README 只留"配置在哪"三行 |
| `### Library / SDK`（118–137） | `docs/guide/rpc-sdk.md` | 已覆盖，README 只留 10 行最小示例 |
| `### Extending`（104–117） | `guide/{skills-templates,extensions,mcp}.md` | 已覆盖，删 |
| `## Architecture`（248–263） | `docs/architecture.md` | 已覆盖，删 |
| `## Status`（358–377） | `CHANGELOG.md` / `docs/feature-map.md` | 删除（README 不再背里程碑） |
| `## API keys`（45–55） | 保留但压成 1 行 + 3 行表 | 第一段必需 |
| Badge 行 | 保留（PyPI / CI / Python） | license badge **待定**（见开放项） |

## 新 README 骨架（≤130 行）

```
# ZenAgent                                    ← 标题 + 3 badge
一句话定位：a coding agent you can read, embed and change
            纯 Python，kernel 只依赖 httpx
4 行 "what it actually does"（读写文件、跑命令、搜索、派子代理、四种用法）
---
## 1. Install                                 ← 第一 part（30 秒）
uv tool install "zenagent-py[cli]"  && zen --version
Python 3.11+ ；API key 三行表；升级/更新检查一句话（ZENAGENT_NO_UPDATE_CHECK=1）
## Quick start                                ← 同一 part 内
zen                    # TUI
zen -p "..." --json     # headless，进脚本/CI
---
## 2. What this is, and why                   ← 第二 part（心脏，F2 主线）
- 一个依赖的 kernel（httpx），wheel 451 KB，启动 0.1 s
- 纯 Python：pip 装、3.11–3.14、无 node runtime、无编译
- 四个接入面共用一套内核：TUI / REPL / headless / `zen rpc`（JSONL）
- 因此 GUI 是客户端不是重写 —— Tauri 应用在路线图上
- 30 行嵌入自己的程序；skills/模板/扩展/MCP/自定义 provider 文件驱动、`/reload` 生效
- 无厂商锁定（openai-completions 含 zai/deepseek 方言 + anthropic-messages，可指本地 endpoint）
- 成本可见（catalog 单价、`zen usage`、`--max-cost`）
---
## 3. Features                                ← 第三 part（6 条，每条 ≤3 行 + docs 链接）
1. 小到能读完的 kernel（1 依赖 / 451 KB / 0.1 s）
2. 四个接入面（含 RPC → Tauri 路线）
3. 嵌入：SDK 30 行
4. 扩展而不重启（skills / templates / extensions / MCP / provider）
5. 模型与成本（多方言、catalog、usage、max-cost）
6. 路线图（Tauri 应用）
每条末尾一个绝对链接指向对应 docs 页面
---
## 4. Contributing                            ← 第四 part
uv sync --extra cli ；bash scripts/check.sh（唯一门禁）
docs 里读什么（六行表：架构 / CLI / 工具与权限 / 配置 / 契约 / 发版）
发版：三条命令 + 指向 docs/development.md
"未做：OS 级沙箱"一句实话 + license 一行
```

## 我替你做的判断（读文档时顺手核对）

1. **去掉 `## Status`**：里程碑是 CHANGELOG 的事，落地页不需要；只保留一句"未做：OS 级沙箱"。
2. **所有跨文件链接用绝对 URL**：为 PyPI 渲染服务，代价是 GitHub 上点击多一跳。
3. **量化数字只写三个**（451 KB wheel / 1 个依赖 / 0.1 s 启动），其余用文字描述——数字越多越容易过期。
4. **工具数量不复述**：写"19 个内置工具（`gh` 存在时 20 个）"，明细留给 `guide/tools-permissions.md`。
5. **特性表按"陌生人决策顺序"排**：小 → 接入面 → 嵌入 → 扩展 → 模型/成本 → 路线图。

## 已定的两件事（2026-11-02，用户决定）

- **`deepswe` 分数：不提**。这条从计划中彻底移除，README 不出现任何未在仓库内可复现的分数。
- **LICENSE：MIT**，新增仓库根 `LICENSE`（`Copyright (c) 2026 Justin Gao`），`pyproject.toml` 用 PEP 639
  字符串形式 `license = "MIT"` + `license-files = ["LICENSE"]`；README 末尾一行 `License: MIT` 链接到它。
