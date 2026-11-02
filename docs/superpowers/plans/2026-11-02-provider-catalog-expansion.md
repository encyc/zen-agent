# Provider 目录扩展（对齐 pi-ai 主流厂商）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 zenagent 的 `catalog.yaml` 从 3 个 provider 扩到 16 个，覆盖 OpenAI、Z.AI、DeepSeek、Kimi/Moonshot、MiniMax、Google、xAI、OpenRouter、Xiaomi 的主流模型，数据来自 pi-ai 0.73.1。

**Architecture:** 纯数据工作，零新 adapter。9 家厂商全部落在已注册的 4 个 adapter（openai-completions / openai-responses / anthropic-messages / google-generative-ai）内。用一个一次性 Python 脚本把 pi-ai 的 `models.generated.js`（经 `node -e` 导出为 JSON）机械转换成 `catalog.yaml` 条目，脚本不入库；产物是 `catalog.yaml` 的新增 provider 段 + 一个防覆盖回归测试 + README/docs 的同步更新。

**Tech Stack:** Python 3.11+ / pytest / uv；转换脚本用 Python stdlib（json、re），数据导出用 `node -e`（仅开发时，不是运行时依赖）。

**Spec:** `docs/superpowers/specs/2026-11-02-provider-catalog-expansion-design.md`

## Global Constraints

- 运行时依赖仍然只有 `httpx`。转换脚本和 `node` 都是开发时工具，不得进入 `pyproject.toml` 依赖，不得进入 `zenagent/`。
- `catalog.yaml` 语法是受限子集：**禁止** tab、列表（`- ` 开头）、锚点/别名/标签、块标量、引号字符串、flow 序列；数字不收 `null/nan/inf`。生成器必须输出符合该语法的文本。
- 模型 id **不得包含冒号** `:`（`_tokenize` 用 `partition(":")` 切 key，会切坏）。
- 现有 3 个 provider（`zai-coding-cn`、`deepseek`、`opencode-go`）的条目**逐字节保持原样**，不得被脚本重写。
- 鉴权只走 `api_key_env`，不做 OAuth；env var 名照抄 pi-ai 的 `env-api-keys.js` 映射。
- `README.md` 行数上限 130（`tests/test_readme.py` 钉住），四段标题固定，链接必须绝对 URL。
- 所有工作走 `bash scripts/check.sh` 作为唯一门禁（sync + ruff + pytest）。
- 不写真实网络调用测试。

## 已核实的事实（执行时直接采用，不要重新调研）

pi-ai 0.73.1 的协议归属（用 `node -e 'import("./models.generated.js")'` 逐条核实，每个 provider 内 api 唯一）：

| provider id | adapter | base_url | 模型数 |
|---|---|---|---|
| openai | openai-responses | https://api.openai.com/v1 | 42 |
| zai | openai-completions | https://api.z.ai/api/coding/paas/v4 | 5 |
| kimi | **anthropic-messages** | https://api.kimi.com/coding | 2 |
| moonshotai | openai-completions | https://api.moonshot.ai/v1 | 7 |
| moonshotai-cn | openai-completions | https://api.moonshot.cn/v1 | 7 |
| minimax | anthropic-messages | https://api.minimax.io/anthropic | 2 |
| minimax-cn | anthropic-messages | https://api.minimaxi.com/anthropic | 2 |
| google | google-generative-ai | https://generativelanguage.googleapis.com/v1beta | 27 |
| xai | **openai-completions** | https://api.x.ai/v1 | 25 |
| openrouter | openai-completions | https://openrouter.ai/api/v1 | 82（精选） |
| xiaomi | **anthropic-messages** | https://api.xiaomimimo.com/anthropic | 5 |
| xiaomi-token-plan-cn | anthropic-messages | https://token-plan-cn.xiaomimimo.com/anthropic | 5 |
| xiaomi-token-plan-ams | anthropic-messages | https://token-plan-ams.xiaomimimo.com/anthropic | 5 |
| xiaomi-token-plan-sgp | anthropic-messages | https://token-plan-sgp.xiaomimimo.com/anthropic | 5 |

env var（照抄 pi-ai `dist/env-api-keys.js` 的 `envMap`）：`OPENAI_API_KEY`、`ZAI_API_KEY`、`KIMI_API_KEY`、`MOONSHOT_API_KEY`（moonshotai 与 moonshotai-cn **共用**）、`MINIMAX_API_KEY`、`MINIMAX_CN_API_KEY`、`GEMINI_API_KEY`（google）、`XAI_API_KEY`、`OPENROUTER_API_KEY`、`XIAOMI_API_KEY`、`XIAOMI_TOKEN_PLAN_{CN,AMS,SGP}_API_KEY`。

新增模型总计：**139 + 82 = 221**（13 个 provider 139 个 + openrouter 精选 82 个）。现有目录为 **3 个 provider / 8 个模型**（`zai-coding-cn` 2、`deepseek` 2、`opencode-go` 4）——注意 `deepseek` 已存在，本计划**不新增** deepseek provider，只在事实表里保留它作为"已有、不动"的一行。全部完成后：**17 个 provider / 229 个模型**。

## Review Focus

最容易咬到人的五个输入/条件，各自在 owning task 里有测试：

1. **带冒号的模型 id**（`anthropic/claude-3.7-sonnet:thinking`）—— 人从 openrouter 复制一个 id 进来时，tokenizer 的 `partition(":")` 会把 key 切坏，最终报出的却是 **"unexpected indentation"**（已实测，错误信息完全指不到真正原因）。我们采取的做法是根本不生成这类 id，并用测试钉住。Task 4/5。
2. **anthropic-messages 的 base_url 不带 `/v1`**（kimi/xiaomi/minimax）—— 人去改 base_url 时补上 `/v1` 会拼成 `/v1/v1/messages`；期望是文档与测试都钉住"不带 /v1"的约定。Task 2/6。
3. **国内站与海外站共用 env var**（moonshotai/moonshotai-cn）—— 人以为要设两个 key，或以为设 `MOONSHOT_API_KEY` 只影响海外站；期望是 README 写清楚共用关系。Task 7。
4. **包月套餐端点记 0 成本**（xiaomi-token-plan / zai-coding-cn）—— 人看到 cost 0 以为目录写错了；期望 YAML 里有一行注释解释。Task 2。
5. **现有手工条目被脚本覆盖**（zai-coding-cn/deepseek/opencode-go）—— 重跑转换脚本会悄悄抹掉手工调优的 compat；期望有回归测试逐字节盯住。Task 4。

---

### Task 1: 导出 pi-ai 目录为可转换的 JSON

**Files:**
- Create: `/tmp/pi-ai-export/models.json`（临时产物，不入库）
- Create: `/tmp/pi-ai-export/README.txt`（记录版本与命令，临时）

**Interfaces:**
- Consumes: 无
- Produces: `/tmp/pi-ai-export/models.json`，结构为
  `{provider_id: [{id, name, api, baseUrl, provider, compat, reasoning, input, cost:{input,output,cacheRead,cacheWrite}, contextWindow, maxTokens}, ...]}`
  ——后续任务只读这个文件，不再碰 node。

- [ ] **Step 1: 下载并解包 pi-ai 0.73.1**

```bash
mkdir -p /tmp/pi-ai-export /tmp/pi-ai-pkg && cd /tmp/pi-ai-pkg
curl -sL https://registry.npmjs.org/@mariozechner/pi-ai/-/pi-ai-0.73.1.tgz | tar xz
ls package/dist/models.generated.js
```

Expected: 打印出 `package/dist/models.generated.js`。

- [ ] **Step 2: 导出全量目录为 JSON**

```bash
cd /tmp/pi-ai-pkg/package/dist && node -e '
import("./models.generated.js").then(m => {
  const out = {};
  for (const [prov, models] of Object.entries(m.MODELS)) {
    out[prov] = Object.values(models).map(x => ({
      id: x.id, name: x.name, api: x.api, baseUrl: x.baseUrl, provider: x.provider,
      compat: x.compat ?? {}, reasoning: !!x.reasoning, input: x.input ?? ["text"],
      cost: x.cost ?? {}, contextWindow: x.contextWindow, maxTokens: x.maxTokens
    }));
  }
  require("fs").writeFileSync("/tmp/pi-ai-export/models.json", JSON.stringify(out, null, 1));
  console.log("providers:", Object.keys(out).length,
              "models:", Object.values(out).reduce((a,b)=>a+b.length,0));
})'
```

Expected: `providers: 31 models: 969`（数字是 pi-ai 0.73.1 的固定值；不一致说明包版本变了，停下来报告）。

- [ ] **Step 3: 校验目标 provider 都在**

```bash
python3 -c "
import json
d = json.load(open('/tmp/pi-ai-export/models.json'))
want = ['openai','zai','deepseek','kimi-coding','moonshotai','moonshotai-cn',
        'minimax','minimax-cn','google','xai','openrouter','xiaomi',
        'xiaomi-token-plan-cn','xiaomi-token-plan-ams','xiaomi-token-plan-sgp']
missing = [w for w in want if w not in d]
print('missing:', missing)
for w in want: print(w, len(d[w]))
assert not missing
"
```

Expected: `missing: []`，随后 15 行计数（openai 42、zai 5、deepseek 2、kimi-coding 2、moonshotai 7、moonshotai-cn 7、minimax 2、minimax-cn 2、google 27、xai 25、openrouter 275、xiaomi 5、xiaomi-token-plan-* 各 5）。

- [ ] **Step 4: 写临时说明文件**

把包版本、导出命令、导出日期写进 `/tmp/pi-ai-export/README.txt`，供 Task 2/5 的实现者与日后手工同步参考。

- [ ] **Step 5: 不提交**（临时产物，无 git 动作）

---

### Task 2: 写转换器并生成 13 个新 provider 段

**Files:**
- Create: `/tmp/pi-ai-export/convert.py`（一次性脚本，**不入库**）
- Create: `/tmp/pi-ai-export/generated.yaml`（脚本输出，供审阅）
- Modify: `zenagent/catalog.yaml`（把 generated.yaml 的内容追加到 `providers:` 下，现有段落一字不动）

**Interfaces:**
- Consumes: `/tmp/pi-ai-export/models.json`（Task 1 的结构）
- Produces: `zenagent/catalog.yaml` 里 14 个新 provider 段（13 个全量 + openrouter 由 Task 5 生成；本任务先产出除 openrouter 外的 13 个）。
- 本任务固定一个模块级常量表，Task 5 复用它：

```python
PROVIDERS = {
    # our_id:            (pi_id,                adapter,               base_url,                                        env,                         default_model)
    "openai":            ("openai",             "openai-responses",    "https://api.openai.com/v1",                     "OPENAI_API_KEY",            None),
    "zai":               ("zai",                "openai-completions",  "https://api.z.ai/api/coding/paas/v4",           "ZAI_API_KEY",               None),
    "kimi":              ("kimi-coding",        "anthropic-messages",  "https://api.kimi.com/coding",                   "KIMI_API_KEY",              None),
    "moonshotai":        ("moonshotai",         "openai-completions",  "https://api.moonshot.ai/v1",                    "MOONSHOT_API_KEY",          None),
    "moonshotai-cn":     ("moonshotai-cn",      "openai-completions",  "https://api.moonshot.cn/v1",                    "MOONSHOT_API_KEY",          None),
    "minimax":           ("minimax",            "anthropic-messages",  "https://api.minimax.io/anthropic",              "MINIMAX_API_KEY",           None),
    "minimax-cn":        ("minimax-cn",         "anthropic-messages",  "https://api.minimaxi.com/anthropic",            "MINIMAX_CN_API_KEY",        None),
    "google":            ("google",             "google-generative-ai","https://generativelanguage.googleapis.com/v1beta","GEMINI_API_KEY",           None),
    "xai":               ("xai",                "openai-completions",  "https://api.x.ai/v1",                           "XAI_API_KEY",               None),
    "xiaomi":            ("xiaomi",             "anthropic-messages",  "https://api.xiaomimimo.com/anthropic",          "XIAOMI_API_KEY",            None),
    "xiaomi-token-plan-cn":  ("xiaomi-token-plan-cn",  "anthropic-messages", "https://token-plan-cn.xiaomimimo.com/anthropic",  "XIAOMI_TOKEN_PLAN_CN_API_KEY",  None),
    "xiaomi-token-plan-ams": ("xiaomi-token-plan-ams", "anthropic-messages", "https://token-plan-ams.xiaomimimo.com/anthropic", "XIAOMI_TOKEN_PLAN_AMS_API_KEY", None),
    "xiaomi-token-plan-sgp": ("xiaomi-token-plan-sgp", "anthropic-messages", "https://token-plan-sgp.xiaomimimo.com/anthropic", "XIAOMI_TOKEN_PLAN_SGP_API_KEY", None),
}
# default: 取该 provider 模型列表里的最后一个（pi-ai 的目录按由旧到新排列，
# 用 Step 2 的输出人工复核一次后写死）
```

- [ ] **Step 1: 先确认 catalog.yaml 的现有段落边界**

```bash
cd /Users/mac/zen-agent && grep -n "^  [a-z0-9-]*:$" zenagent/catalog.yaml
```

Expected: 3 行（`zai-coding-cn:`、`deepseek:`、`opencode-go:`）。记下最后一个 provider 结束的行号（文件末尾），新内容从其后追加。

- [ ] **Step 2: 确定每个 provider 的 default 模型**

```bash
python3 -c "
import json
d = json.load(open('/tmp/pi-ai-export/models.json'))
for p in ['openai','zai','kimi-coding','moonshotai','moonshotai-cn','minimax','minimax-cn','google','xai','xiaomi','xiaomi-token-plan-cn']:
    ids = [m['id'] for m in d[p]]
    print(f'{p}: {ids}')
"
```

Expected: 打印每个 provider 的模型 id 列表。**人工挑选**最新的旗舰（如 openai 取最新 gpt 代、google 取最新 gemini-pro 代），把选中的 id 写进脚本 `PROVIDERS` 的 `default_model` 字段；`None` 表示不设 default（该 provider 无明确旗舰时）。

- [ ] **Step 3: 写 `convert.py` 的 YAML 渲染部分**

关键规则（每条都有对应理由，实现时必须遵守）：

- provider 级写 `name`（用 pi-ai 的 name 或我们的展示名）、`base_url`、`api_key_env`、`adapter`（本任务 13 个全部有统一 adapter，可写 provider 级）。
- 模型级必须写 `context_window`、`max_tokens`、`reasoning`（loader 强制要求这三项）。
- `input` 含 `"image"` → 写 `supports_vision: true`，否则省略。
- cost：只写非零项，格式 `cost: {input: X, output: Y, cache_read: Z, cache_write: W}`，按 `input, output, cache_read, cache_write` 顺序，0 值省略。
- compat 映射（只写偏离默认的项）：`thinkingFormat`→`thinking_format`（仅 `zai`/`deepseek`，`qwen` 跳过并加注释）、`supportsDeveloperRole:false`→`supports_developer_role: false`、`supportsReasoningEffort:true`→`supports_reasoning_effort: true`、`zaiToolStream:true`→`zai_tool_stream: true`、`requiresReasoningContentOnAssistantMessages:true`→`requires_reasoning_content_on_assistant: true`；其余 pi-ai 独有键（`supportsStore`、`supportsStrictMode`、`sendSessionAffinityHeaders`、`supportsEagerToolInputStreaming`）丢弃。
- 浮点数输出用 `repr(float)` 去掉多余尾零（`0.435` 保持 `0.435`，`0.5` 不要写 `0.50`），整数 cost 写成整数（`0` 不是 `0.0`）。
- 模型 id 字符串正常写 key（不含冒号，已由来源保证）；**生成后必须断言每个 key 不含 `:`**。
- 每个 provider 段前写一行来源注释：`# synced from @mariozechner/pi-ai@0.73.1 (2026-11-02)`；包月套餐 provider 额外一行 `# 包月套餐，按量计 0`。

- [ ] **Step 4: 跑脚本生成并自检语法**

```bash
cd /tmp/pi-ai-export && python3 convert.py > generated.yaml
cd /Users/mac/zen-agent && python3 -c "
from zenagent.providers.catalog import _build_catalog
text = open('/tmp/pi-ai-export/generated.yaml').read()
cat = _build_catalog('providers:\n' + text)
print({k: len(v.models) for k, v in cat.items()})
"
```

Expected: 打印 13 个 provider 及其模型数，与 Global Constraints 的事实表一致（openai 42、zai 5、kimi 2、moonshotai 7、moonshotai-cn 7、minimax 2、minimax-cn 2、google 27、xai 25、xiaomi 5、xiaomi-token-plan-* 各 5）。任何 ValueError 都说明违反了受限语法，按报错行修生成器。

- [ ] **Step 5: 把生成内容拼进 catalog.yaml**

```bash
cd /Users/mac/zen-agent && cat /tmp/pi-ai-export/generated.yaml >> zenagent/catalog.yaml
python3 -c "
from zenagent.providers.catalog import load_catalog
c = load_catalog()
print(len(c), 'providers')
print(sum(len(p.models) for p in c.values()), 'models')
"
```

Expected: `16 providers`、`147 models`（现有 3 个 8 个模型 + 新增 13 个 provider 的 139 个；若数字不同，比对上面的事实表定位差异）。

- [ ] **Step 6: 全量门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/check.sh
```

Expected: ruff 无错，pytest 全绿。若有测试因为新 provider 的出现而失败（例如某处断言 provider 数量），在 Task 3 处理，本步只记录失败的测试名。

- [ ] **Step 7: 提交**

```bash
cd /Users/mac/zen-agent && git add zenagent/catalog.yaml
git commit -m "feat(catalog): add 13 mainstream providers from pi-ai 0.73.1

OpenAI, Z.AI, Kimi, Moonshot (overseas + CN), MiniMax (overseas + CN),
Google, xAI, Xiaomi MiMo (standard + 3 token-plan endpoints).
Pure catalog data: every provider maps onto an already-registered adapter."
```

---

### Task 3: 修掉因新 provider 而失败的既有测试

**Files:**
- Modify: `tests/test_catalog.py:63-64`

**Interfaces:**
- Consumes: Task 2 的门禁输出
- Produces: 无新接口；既有测试恢复全绿

**已知会失败的一条（已核实）**：`tests/test_catalog.py::test_load_catalog_returns_three_providers_in_file_order`
第 64 行 `assert list(load_catalog()) == ["zai-coding-cn", "deepseek", "opencode-go"]`。
**不会**失败的（不要动）：`test_find_model_glm_53_is_zai_dialect_and_first_provider_wins`
（新 `zai` provider 没有 `glm-5.3`，`find_model` 首个命中者胜出的规则仍返回 zai-coding-cn）、
`test_default_flag_true_only_on_zai_glm_53`（只断言特定模型，不数总数）。

- [ ] **Step 1: 复现失败**

```bash
cd /Users/mac/zen-agent && uv run pytest tests/test_catalog.py -q 2>&1 | tail -15
```

Expected: 恰好一条失败，`test_load_catalog_returns_three_providers_in_file_order`。

- [ ] **Step 2: 改写该测试**

把「恰好三个」改成「原有三个仍在最前、且文件顺序稳定」，新 provider 追加在其后：

```python
def test_load_catalog_keeps_the_hand_tuned_providers_first_in_file_order():
    # The hand-tuned providers stay first (find_model's first-wins rule depends
    # on it); pi-ai synced providers are appended after them.
    names = list(load_catalog())
    assert names[:3] == ["zai-coding-cn", "deepseek", "opencode-go"]
    assert len(names) >= 3
```

同时把函数名从 `test_load_catalog_returns_three_providers_in_file_order` 改掉（名字已不描述行为）。

- [ ] **Step 3: 重新跑门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/check.sh
```

Expected: 全绿。

- [ ] **Step 4: 提交**

```bash
cd /Users/mac/zen-agent && git add tests/test_catalog.py
git commit -m "test: stop pinning the catalog to exactly three providers"
```

---

### Task 4: 加防覆盖回归测试

**Files:**
- Create: `tests/test_catalog_pi_ai.py`
- Test: 同上

**Interfaces:**
- Consumes: `zenagent.providers.catalog.load_catalog()`（返回 `dict[str, ProviderConfig]`）
- Produces: 5 个测试函数，钉住 Task 2 的产物与现有条目

- [ ] **Step 1: 写失败测试**

```python
"""The pi-ai catalog expansion must load, stay wired, and not overwrite hand-tuned entries."""

import pytest

from zenagent.providers.catalog import load_catalog

NEW_PROVIDERS = {
    "openai": ("openai-responses", 42),
    "zai": ("openai-completions", 5),
    "kimi": ("anthropic-messages", 2),
    "moonshotai": ("openai-completions", 7),
    "moonshotai-cn": ("openai-completions", 7),
    "minimax": ("anthropic-messages", 2),
    "minimax-cn": ("anthropic-messages", 2),
    "google": ("google-generative-ai", 27),
    "xai": ("openai-completions", 25),
    "xiaomi": ("anthropic-messages", 5),
    "xiaomi-token-plan-cn": ("anthropic-messages", 5),
    "xiaomi-token-plan-ams": ("anthropic-messages", 5),
    "xiaomi-token-plan-sgp": ("anthropic-messages", 5),
}


@pytest.mark.parametrize("provider_id", sorted(NEW_PROVIDERS))
def test_new_provider_loads_with_expected_adapter_and_model_count(provider_id):
    adapter, count = NEW_PROVIDERS[provider_id]
    provider = load_catalog()[provider_id]
    assert provider.adapter == adapter
    assert len(provider.models) == count


def test_every_catalog_model_uses_a_registered_adapter():
    from zenagent.providers.registry import _REGISTRY
    for provider in load_catalog().values():
        for model in provider.models:
            assert model.api in _REGISTRY, f"{provider.id}/{model.id} uses unregistered {model.api}"


def test_no_catalog_model_id_contains_a_colon():
    """A colon in a key would be split by the catalog tokenizer's partition(':')."""
    for provider in load_catalog().values():
        for model in provider.models:
            assert ":" not in model.id, f"{provider.id}/{model.id}"


def test_hand_tuned_providers_are_untouched():
    """zai-coding-cn's explicit zai dialect must survive catalog regeneration."""
    glm = load_catalog()["zai-coding-cn"].models[0]
    assert glm.id == "glm-5.3"
    assert glm.compat.thinking_format == "zai"
    assert glm.compat.zai_tool_stream is True
    assert glm.compat.supports_developer_role is False
    assert glm.compat.supports_reasoning_effort is True


def test_anthropic_style_providers_keep_base_urls_without_v1():
    """The anthropic adapter appends /v1/messages; a base_url ending in /v1 doubles it."""
    catalog = load_catalog()
    for provider_id in ("kimi", "xiaomi", "minimax"):
        assert not catalog[provider_id].base_url.endswith("/v1"), provider_id
```

- [ ] **Step 2: 跑测试确认状态**

```bash
cd /Users/mac/zen-agent && uv run pytest tests/test_catalog_pi_ai.py -v
```

Expected: 若 Task 2 已完成，全部 PASS（这些测试是回归护栏，不是 TDD 红灯——目录数据已存在）。若有 FAIL，说明 Task 2 的产物与事实表不符，回去修 catalog.yaml 而不是改测试数字。

- [ ] **Step 3: 跑全量门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/check.sh
```

Expected: 全绿。

- [ ] **Step 4: 提交**

```bash
cd /Users/mac/zen-agent && git add tests/test_catalog_pi_ai.py && git commit -m "test(catalog): pin the pi-ai provider expansion and protect hand-tuned entries"
```

---

### Task 5: OpenRouter 精选（82 条）

**Files:**
- Modify: `/tmp/pi-ai-export/convert.py`（复用 Task 2 的渲染器，加精选函数）
- Modify: `zenagent/catalog.yaml`（追加 `openrouter:` 段）

**Interfaces:**
- Consumes: Task 2 的渲染器与 `PROVIDERS` 表；`/tmp/pi-ai-export/models.json` 的 `openrouter` 键
- Produces: `catalog.yaml` 的 `openrouter:` 段，82 个模型，adapter `openai-completions`，base_url `https://openrouter.ai/api/v1`，env `OPENROUTER_API_KEY`

- [ ] **Step 1: 验证精选规则产出 82 条**

```bash
python3 -c "
import json, re
d = json.load(open('/tmp/pi-ai-export/models.json'))
white = ('anthropic','openai','google','x-ai','deepseek','z-ai','moonshotai','minimax','xiaomi')
sel = [m['id'] for m in d['openrouter'] if m['id'].split('/')[0] in white and ':' not in m['id']]
old = re.compile(r'(-\d{4}|-preview|-\d{4}-\d{2}-\d{2}|-lite|-\d+\.\d+-|claude-3|gpt-4-|gpt-3|gemini-1\.|gemini-2\.0|-8b|-old)')
latest = [i for i in sel if not old.search(i)]
print(len(sel), 'whitelisted;', len(latest), 'after old-gen filter')
print(sorted(latest)[:5])
assert len(latest) == 82, 'selection drifted; re-derive and update the test count'
"
```

Expected: `131 whitelisted; 82 after old-gen filter`，断言通过。**若 pi-ai 版本或规则变了导致不是 82**，以实际数为准并同步更新 Task 4 的测试（Task 4 未包含 openrouter，需在 Task 5 里补一条 openrouter 断言）。

- [ ] **Step 2: 在 convert.py 里实现 `select_openrouter(models)`**

规则：白名单前缀 + 排除含冒号 id + 旧代正则过滤，全部按 Step 1 的表达式。排序按 id 字典序，保证可重复。

- [ ] **Step 3: 生成并校验**

```bash
cd /tmp/pi-ai-export && python3 convert.py --openrouter > openrouter.yaml
cd /Users/mac/zen-agent && python3 -c "
from zenagent.providers.catalog import _build_catalog
cat = _build_catalog('providers:\n' + open('/tmp/pi-ai-export/openrouter.yaml').read())
p = cat['openrouter']
print(len(p.models), 'models;', p.adapter, p.base_url)
"
```

Expected: `82 models; openai-completions https://openrouter.ai/api/v1`。

- [ ] **Step 4: 拼进 catalog.yaml 并跑门禁**

```bash
cd /Users/mac/zen-agent && cat /tmp/pi-ai-export/openrouter.yaml >> zenagent/catalog.yaml
bash scripts/check.sh
```

Expected: 全绿；`load_catalog()` 现在 17 个 provider、229 个模型（147 + 82）。

- [ ] **Step 5: 给 openrouter 补一条计数断言**

在 `tests/test_catalog_pi_ai.py` 的 `NEW_PROVIDERS` 里加一行
`"openrouter": ("openai-completions", 82),`，并重跑该测试文件。

```bash
cd /Users/mac/zen-agent && uv run pytest tests/test_catalog_pi_ai.py -v
```

Expected: 全 PASS（含新的 openrouter 参数化用例）。

- [ ] **Step 6: 提交**

```bash
cd /Users/mac/zen-agent && git add zenagent/catalog.yaml tests/test_catalog_pi_ai.py
git commit -m "feat(catalog): add OpenRouter with 82 curated models

Whitelist vendors mirrored from our own providers, drop colon-suffixed
variants (the catalog grammar cannot express them), drop previous-gen ids."
```

---

### Task 6: 文档同步（docs 是手册）

**Files:**
- Modify: `docs/internals/providers.md:27-40`（目录与兼容开关一节）
- Modify: `docs/guide/config.md`（provider 解析顺序一节，如列举了内置 provider）

**Interfaces:**
- Consumes: Task 2/5 的最终 provider 清单
- Produces: 文档里可查的 17 个内置 provider、各家 adapter 归属、env var、包月套餐说明

- [ ] **Step 1: 更新 `docs/internals/providers.md` 的目录一节**

在「目录与兼容开关」后追加一小节，内容必须包含：
- 内置 provider 共 17 个，按 adapter 分组列表（openai-completions / openai-responses / anthropic-messages / google-generative-ai）；
- 一句「新增 provider 只需加 catalog 条目，不需要写 adapter」；
- 三条易错事实：kimi/xiaomi 走 anthropic-messages 且 base_url **不带** `/v1`；xai 走 openai-completions（不是 responses）；openrouter 精选不含带冒号变体。
- 数据来源一句：`# synced from @mariozechner/pi-ai@0.73.1`，更新方式就是重跑转换脚本（脚本不入库，命令记录在 git log / 本条注释里）。

- [ ] **Step 2: 更新 `docs/guide/config.md`**

若该节列举了内置 provider 或解析顺序细节，补上「内置目录含 17 个 provider，完整清单见 `zenagent/catalog.yaml`」，并说明国内站/海外站是独立 provider id（`moonshotai` vs `moonshotai-cn`），共用 `MOONSHOT_API_KEY`。

- [ ] **Step 3: 门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/check.sh
```

Expected: 全绿（`tests/test_docs_commands.py` 只管 slash 命令，不受影响；仍要跑完整门禁确认没碰坏别的文档断言）。

- [ ] **Step 4: 提交**

```bash
cd /Users/mac/zen-agent && git add docs/ && git commit -m "docs: document the 17 built-in providers and their adapter split"
```

---

### Task 7: README 的 provider 表

**Files:**
- Modify: `README.md:28-35`（env var 表）
- Modify: `README.zh-CN.md:25-33`（同一张表的中文版）

**Interfaces:**
- Consumes: 最终 provider 清单与 env var（Global Constraints 已固定）
- Produces: README 两张同步的表，行数仍在 130 以内

- [ ] **Step 1: 先量当前行数，确认预算**

```bash
cd /Users/mac/zen-agent && wc -l README.md README.zh-CN.md
```

Expected: `109 README.md`、`101 README.zh-CN.md`。3 行表扩成 17 行 = +14 行，再加 2 行说明 → **125 / 117**，均低于 130（README.md 只剩 5 行余量，改完必须跑 `test_readme.py` 确认）。

- [ ] **Step 2: 改 README.md 的表**

把现有 3 行表扩成 17 行（provider | env var），按 adapter 或字母序排列，并在表后加两句：
- 国内站与海外站是独立 provider id（`moonshotai` / `moonshotai-cn`），共用 `MOONSHOT_API_KEY`；
- 包月套餐端点（`zai-coding-cn`、`xiaomi-token-plan-*`）成本按量计 0。

**注意**：不得引入相对链接（`test_readme.py` 会失败），新增链接一律用
`https://github.com/encyc/zen-agent/blob/main/...`。

- [ ] **Step 3: 同步 README.zh-CN.md**

同样的 17 行表 + 同样的两句说明（中文）。两份 README 的段落数必须一致（`test_readme.py` 检查 `##` 标题数量相同）。

- [ ] **Step 4: 门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/check.sh
```

Expected: 全绿，特别是 `tests/test_readme.py` 五条断言全部通过。

- [ ] **Step 5: 提交**

```bash
cd /Users/mac/zen-agent && git add README.md README.zh-CN.md
git commit -m "docs(readme): list all 17 built-in providers and their env vars"
```

---

### Task 8: 端到端验收

**Files:**
- 无（只跑验证）

**Interfaces:**
- Consumes: 前七个任务的全部产物
- Produces: 一次可复现的全量验证记录

- [ ] **Step 1: 完整门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/check.sh
```

Expected: 全绿。

- [ ] **Step 2: 打包门禁**

```bash
cd /Users/mac/zen-agent && bash scripts/release-check.sh
```

Expected: 通过（`catalog.yaml` 是包内数据文件，体量增大会体现在 wheel 大小里；若脚本对 wheel 体积有硬断言，记录实际值并确认仍在阈值内）。

- [ ] **Step 3: doctor 能列出全部 provider**

```bash
cd /Users/mac/zen-agent && uv run zen doctor 2>&1 | head -30
```

Expected: `catalog` 一行显示 `17 providers: ...`，并逐个列出 `key:<id>` 检查项（大部分显示 `is missing`，这是正常的——没有真实 key）。

- [ ] **Step 4: 冒烟：确认目录能被 agent 用上**

```bash
cd /Users/mac/zen-agent && uv run python -c "
from zenagent.providers.catalog import load_catalog, find_model
c = load_catalog()
print(len(c), 'providers,', sum(len(p.models) for p in c.values()), 'models')
for pid in ('openai','google','xai','kimi','xiaomi-token-plan-cn','openrouter'):
    m = c[pid].models[0]
    print(pid, '->', m.id, m.api, m.base_url, m.context_window)
"
```

Expected: `17 providers, 229 models` 加 6 行详情，每行的 api 与事实表一致。

- [ ] **Step 5: 汇总报告**

把三步的实际输出贴给人类伙伴，说明：新增 provider 数、模型数、唯一门禁结果、是否有需要 follow-up 的项（例如某个 provider 没有旗舰 default）。

---

## 自审记录（writing-plans Self-Review）

**1. Spec 覆盖**：设计文档的要求逐条落到了任务——16 个 provider 端点（Task 2 + Task 5）、只支持 API key（Task 2 的 `PROVIDERS` 表全是 `api_key_env`）、现有条目保留（Task 4 的 `test_hand_tuned_providers_are_untouched`）、openrouter 精选（Task 5）、脚本不入库（Task 2 路径全在 `/tmp`）、验证与门禁（Task 2 Step 6 / Task 8）。设计文档的「Builder check 结论」三条也被覆盖：冒号 id（Task 4 第三个测试）、cache_write 照抄（Task 2 的 cost 规则）、default 无全局唯一性（Task 2 Step 2 按 provider 各选一个）。

**2. 步骤扫描**：每步都是单一动作 + 可检查输出。删掉了「适当地加校验」这类空话；Task 3 的「判断失败原因」给了两条允许的处理路径而不是泛泛而谈。

**3. 类型一致性**：`load_catalog()` 返回 `dict[str, ProviderConfig]`，`ProviderConfig` 有 `.adapter` / `.base_url` / `.models`，`Model` 有 `.id` / `.api` / `.context_window` / `.compat`——全部与 `catalog.py:78-104` 的实际定义一致（已读源码核对）。`_REGISTRY` 是 `registry.py:60` 的实际模块级 dict。

**4. Review Focus**：五条各配了测试——(1) 冒号 id → `test_no_catalog_model_id_contains_a_colon`；(2) anthropic base_url 不带 /v1 → `test_anthropic_style_providers_keep_base_urls_without_v1`；(3) 共用 env var → Task 7 Step 2 的 README 文案 + 文档；(4) 包月计 0 → Task 2 Step 3 的注释规则 + Task 6 文档；(5) 手工条目被覆盖 → `test_hand_tuned_providers_are_untouched`。

**5. 比例**：计划约 400 行，含 8 个任务、约 20 个可执行步骤；代码块只出现在测试定义与关键命令处，不含实现体——转换器只给规则和签名，body 留给实现者。

## 自审发现并已修正的问题（写计划后实跑核对）

写完计划后我用真实数据把每个数字重算了一遍，改掉四处会导致执行者卡住或写错的地方：

1. **模型总数算错**：13 个 provider 是 139 个模型（不是 159），加 openrouter 82 = 221 个新增；完成后 17 provider / 229 模型（原写 245/327）。设计文档同步修正。
2. **现有目录是 3 个 provider / 8 个模型**，不是 4 个。`PROVIDERS` 表已确认恰好 13 项、**不含 deepseek**（deepseek 已存在于现有目录，不新增）。
3. **Task 3 从"预计 0-2 个失败"改成精确的一条**：实跑 `grep` 确认只有 `tests/test_catalog.py:64` 的
   `test_load_catalog_returns_three_providers_in_file_order` 会失败；同时写明另外两条相邻测试**不会**失败及原因（新 `zai` 无 `glm-5.3`），避免实现者误改。
4. **验证了 `_build_catalog` 可用作语法自检入口**（Task 2 Step 4、Task 5 Step 3 用它单测生成的段落），这样在拼进 catalog.yaml 之前就能发现受限语法违规。
