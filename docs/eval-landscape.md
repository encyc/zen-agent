# Agent / harness 任务测试集调研

> 目的：给 pyagent 找"能真正验证 harness 好不好用"的任务集，并给出接入与成本估算。
> 调研日期：2026-10-29。所有数字来自下列来源，接入前需以官方当期版本为准。

## 0. 先分清两类东西

| 类型 | 测什么 | 代表 | 对 pyagent 的意义 |
|---|---|---|---|
| **行为评测（behavioral evals）** | 自己的 harness 在特定行为上是否退化：能不能调用工具、上下文/文档是否被用上、UI/命令是否工作 | pi 自带 `packages/evals`；HAL | 便宜、可进 CI、没有 Docker 依赖；适合回归 |
| **公开基准（benchmarks）** | 完成任务的能力（修 bug、终端操作、客服对话） | Terminal-Bench、SWE-bench、τ-bench、Aider Polyglot | 贵、重、需要容器；适合阶段性对外可比 |

pyagent 两个都需要，但顺序相反：先把行为评测做起来（每天跑），公开基准只在里程碑节点跑。

## 1. 公开基准清单

| 基准 | 是否 agentic | 任务数 | 形态 | 运行要求 | 适用性 |
|---|---|---|---|---|---|
| **Terminal-Bench v2.0** | 是（完整终端、多步骤、工具编排） | 89 | Docker 沙箱任务，完成后跑确定性校验 | Docker；三种接入方式（`AbstractInstalledAgent` 装在容器里 / `BaseAgent` Python API / `MCPAgent` 走网络） | **harness 层首选**：正是"多轮 + 工具 + 终端"这件事 |
| **SWE-bench Verified** | 是（真实 GitHub issue，跨仓库定位+改码） | 500 | agent 提交 patch，harness 跑仓库测试 | Docker/容器编排，单任务分钟级 | 编码能力金标准；**有污染争议**，OpenAI 自 2026 初不再报分 |
| **SWE-bench Pro** | 是 | 1,865 | 多语言、含私有代码库 | 更重 | 若要跨模型比较、规避污染，用 Pro 替代 Verified |
| **Aider Polyglot** | 半 agentic（2 次尝试，可用测试反馈） | 225 | 6 种语言，改代码通过测试 | 较简单，Aider 原生 | 补充项，看"带测试反馈的迭代能力" |
| **τ-bench 2.0（τ³）** | 是（工具 + 用户模拟器 + 策略约束） | text-v1：50 任务/域 × 3 域 × 6 语言 = **900 calls** | 客服域（airline/retail/telecom），有 agent 开发指南 | Python 3.12–3.13、LiteLLM；voice 需 portaudio/ffmpeg | 测"工具调用 + 多轮对话 + 遵守策略"，**session 模式** |
| **Kaggle SAE（Standardized Agent Exams）** | 是 | 按考试批次 | 通过 HTTP 考试 API | 需 Kaggle agent 凭证 | 现成排行榜，但绑定 Kaggle |
| HumanEval / MBPP / BigCodeBench / LiveCodeBench | 否（单轮函数生成） | 164 / 974 / 1,140 / ~1,055 | 模型级 | 无 | **不适用**（饱和且不测 agent 能力） |
| GAIA / OSWorld / MLE-bench | 是，但方向不同 | — | 网页/桌面/MLE | 重 | 与 coding harness 关系弱，暂不纳入 |

### 污染与可复现性（必须写进报告）

- SWE-bench Verified 存在模型复现金 patch 的现象；跨模型比较建议用 Pro，或只做"同模型不同后端"的对比（污染对所有格同等影响）。
- 基准版本要 pin（例如 `terminal-bench==2.0.x`），模型版本也要记录 API 返回的 model 字符串。
- 每格至少跑 3 次，报均值与标准差；结论要超过 2σ 才算数。

## 2. 编排层：kbench（最省事的接入路径）

`shareAI-lab/kbench` 把 SWE-bench Verified、Terminal-Bench 2.0、τ-bench、Kaggle SAE 统一成一个 CLI：

```bash
kbench benchmark list          # swe / tb2 / tau / sae
kbench harness list            # kode-agent-sdk / codex / claude-code / gemini-cli / custom-adapter
kbench run --benchmark tb2 --harness custom-adapter --adapter <dir> --instruction "..."
```

**关键点：它有一个 `custom-adapter` 协议**，天然适合 pyagent 这种 CLI agent：

- 运行时从 `$KBENCH_ADAPTER_INPUT`（或 stdin）读**一个 JSON payload**，向 stdout 写**一个 JSON output payload**；
- `adapter generate --hint cli-harness` 能生成脚手架，用 `KBENCH_CLI_COMMAND` / `KBENCH_CLI_PROMPT_FLAG` / `KBENCH_CLI_OUTPUT_FLAG` 等环境变量桥接任意 CLI；
- 产物标准化：`instances/<id>/result.json`、`trace/normalized/trajectory.json`、`artifacts/`。

对 pyagent 来说，这意味着**不用自己写 benchmark runner**，只需要新增一个 `pyagent adapter` 子命令（读 payload → 跑一次 headless → 输出 JSON）。

## 3. 行为评测：pi 的做法（可直接抄）

pi 的 `packages/evals` 只有 **10 个 eval 文件 / ~2400 行 runner**，跑的是"自己的 harness"而不是公开基准：

- `*.eval.ts`：普通行为用例（如 `smoke.eval.ts`：问一个事实性问题，断言输出、无错误、`usage` 的 provider/model 匹配、token > 0）；
- `*.docs.eval.ts`：**文档 lift**——同一任务在"没有文档"和"有文档"两个隔离容器里各跑一遍，比较提升；
- 容器化：`docker.ts` 构建镜像、`plan.ts` 展开 `(case, variant, repetition)`、`report.ts` 配对两次运行算 lift；
- 用 `vitest-evals` 组织，配一个假 provider 服务器（`acme-server.ts`）做确定性回归。

**对 pyagent 的直接启发**：S1 加的 `AGENTS.md` 注入，正好可以用同样的 A/B 方式验证——同一 repo 任务，带/不带 `AGENTS.md` 各跑 N 次，比较通过率与 token 成本。

## 4. HAL：报告什么（指标清单）

Princeton 的 HAL（ICLR 2026）把评测基础设施化，核心结论值得抄：

- 统一接口：agent 只需暴露 `run(input) -> dict(responses)`；
- **必须记录 cost / tokens / trajectory**，而不是只报准确率；
- 准确率-成本 Pareto：贵模型经常不在前沿；同一模型改 scaffold 会同时改变准确率与成本；
- 自动日志审查能抓到"作弊"（去 HuggingFace 搜答案）与灾难性动作；
- 反直觉结论：**提高 reasoning effort 在 21/36 的对比里没有提升准确率**。

对 pyagent 的落地：`--json` 已经在报 usage/cost/termination，再补一个 `trace` 落盘就能对齐 HAL 的最小要求。

## 5. 推荐路线（按性价比排序）

### E0：仓库内行为评测 ✅ 已完成（2026-10-29）

已落地：`evals/cases/*.json`（13 个用例）+ `scripts/eval.py` + `scripts/eval.sh`，
覆盖 S1 的全部新面：问答、读/写/改文件、grep/glob、bash 成功与失败、
危险命令拒绝、多步修 bug、图片识别、AGENTS.md A/B。3 次重复 13/13 通过，
成本约 $0.012/轮、9.6 万 tokens。用法见 `evals/README.md`。

下面是原始规划（保留作对照）：

- 位置：`evals/` 目录 + `scripts/eval.sh`；
- 形态：YAML/JSON 任务文件 `{id, prompt, setup, assert}`，断言输出子串/正则、工具调用序列、`termination`、`usage.cost` 上限；
- 规模：先 15–25 个用例，覆盖：纯问答、读文件+回答、改文件后验证、grep/glob、bash 失败恢复、拒绝危险命令、图片、上下文压缩、会话 resume；
- 运行：`uv run python scripts/eval.py --model deepseek-flash`，每用例 3 次；
- 成本量级：25 用例 × 3 次 × ~30k tokens ≈ 2.3M tokens ≈ **$1–3/轮**；
- 产出：通过率、平均 token、平均耗时、失败分类（工具误用/循环/提前宣布完成）。

### E1：Terminal-Bench 2.0 子集（2–3 天，harness 层的真实验证）

- 通过 kbench `custom-adapter` 接 pyagent；
- 先跑 10–20 个任务（`task_limit`）验证适配器，再跑全量 89；
- 需要 Docker；每任务默认 30 分钟超时；
- 成本：89 任务 × ~150k tokens ≈ 13M tokens ≈ **$5–20/次**（DeepSeek 峰时价，含输出）。

### E2：SWE-bench Verified 子集（按需，编码能力）

- 先 20–50 个任务，`swe` 需要仓库级容器，单任务更慢；
- 若要对外报分，改 SWE-bench Pro；
- 成本：50 任务 × ~300k tokens ≈ 15M tokens + 容器时间，**$20–60/次** 起。

### E3：τ-bench（可选，测"工具+用户模拟"）

- 走 kbench 的 **session 模式**：需要 adapter 支持 `--messages-file` / `--tools-file`；
- pyagent 目前的 headless 是 task 模式，接 session 模式需要额外适配（工作量最大的一项）。

## 6. pyagent 需要补的接口（E1 前置）

1. `pyagent adapter`：读 `$KBENCH_ADAPTER_INPUT`/stdin 的 JSON → 调 `run_headless` → 输出 `{output, usage, termination, trace?}`；
2. `--json` 增加 `termination`、`cache` 字段（已有），补 `trace` 落盘（工具序列）；
3. 一个 `--cwd` 参数（benchmark 会把 agent 放进任务目录）；
4. 非交互的权限策略：benchmark 环境需要 `--yes`，但要保留危险命令拦截（否则任务可能被拒绝执行）。

## 7. 参考

- Terminal-Bench v2.0 与选型理由：[Red Hat coding-agent-evaluation](https://github.com/red-hat-data-services/agentic-starter-kits/blob/main/docs/coding-agent-evaluation.md)
- 编排与 custom-adapter：[shareAI-lab/kbench](https://github.com/shareAI-lab/kbench)
- τ-bench 2.0：[sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench)
- HAL（ICLR 2026）：[OpenReview](https://openreview.net/forum?id=vUaY1t64ZZ) · [hal.cs.princeton.edu](https://hal.cs.princeton.edu)
- 榜单参考：[Terminal-Bench 2.0 leaderboard](https://snorkel.ai/leaderboard/terminal-bench-2-0/)
- pi 自带 evals：`/Users/mac/pi/packages/evals`（smoke + docs-lift，容器化）
