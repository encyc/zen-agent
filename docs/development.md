# 开发与发版

## 环境

```bash
uv sync --extra cli        # kernel + 交互层（prompt_toolkit / rich / textual）+ dev 工具
uv run zen doctor          # 环境自检：python、key、catalog、config、sessions、终端
```

包本身声明 `requires-python = ">=3.11"`（3.11–3.14 都发），但仓库里的 `.python-version` 固定为
**3.15**：那是 `uv` 开发工具链的 pin，不是使用者的门槛。装 `zenagent-py` 只需要 3.11+。

## 三个闸门

| 闸门 | 命令 | 什么时候跑 | 说明 |
|---|---|---|---|
| 唯一门禁（CI 跑的完全一致） | `bash scripts/check.sh` | 每次提交前 | `uv sync --extra cli --dev` → `ruff check` → `pytest` |
| 全量测试 | `uv run pytest -q` | 调试时单独跑 | **全离线**：fake stream 函数 + 真实 SSE 录制回放，不发网络请求 |
| 行为评测（花钱，可选） | `./scripts/eval.sh` | 里程碑前 | 用真实模型跑 `evals/cases`，验证 harness 行为而不是代码 |

`scripts/check.sh` 会先检查 `.venv` 是否被移动过（venv 会把绝对路径写进 shebang），必要时重建，
所以"换个目录就 `bad interpreter`"这类问题在这里就被吃掉。

行为评测的常用形态：

```bash
./scripts/eval.sh --list                                  # 用例 id
./scripts/eval.sh --model deepseek-flash --repeats 3       # 指定模型、重复次数
./scripts/eval.sh --baseline BASELINE.json                 # 与基线对比，有回归 exit 1
```

`scripts/capture_sse.py` 把真实 provider 的流量录进 `zenagent/providers/fixtures/`，回放测试在录制
缺失时自动 skip——所以要加一个 provider 行为测试，先录一条。

## 打包门禁

`bash scripts/release-check.sh` 是发布前的打包断言（约 3 秒，缓存热时），它**不联网、不发布**，
只往临时目录写：

- 版本一致性：`pyproject.toml`、`zenagent/__init__.py` 与 HEAD 上的 tag 三者一致；
- 产物内容：sdist / wheel 里不含 `tests/`、`docs/`、`evals/`、示例与编辑残留，也不含空文件，
  且 `README.md` 在 sdist 里；
- kernel-only 安装（`pip install zenagent-py`，不带 extra）能 import、`zen --version` 正常，
  并且在只有 kernel 时会提示去装 `[cli]` 而不是崩；
- `[cli]` 安装能启动：`zen --version`/`--help`、TUI/REPL/RPC/doctor 四个前端 import、搜索 provider
  列表、在隔离 `$HOME` 里做一次 settings 往返（权限 600、`describe()` 不含密钥）。

可选参数：`--python 3.13` 换解释器验 `requires-python` 下限，`--skip-build` 校验已有的 `dist/`。

## 发版

`zenagent-py` 从 tag 出货，tag 之前的一切都在本地跑完：

```bash
bash scripts/check.sh            # 1. lint + 全量测试
bash scripts/release-check.sh    # 2. 构建 sdist/wheel，两种安装各验一遍
git tag vX.Y.Z                   # 3. 打 tag（轻量 tag，与历史一致）
git push origin main vX.Y.Z      # 4. push tag 触发 publish workflow
```

`.github/workflows/publish.yml` 在 tag 上重跑同一个打包门禁，通过后上传 PyPI。**手动触发永远是
dry run**——它构建并校验，但不会上传；只有 `v*` tag 才能到 PyPI：

```bash
gh workflow run publish.yml      # 可选：在 CI 上 dry run 一次
```

`CHANGELOG.md` 的版本号必须与 tag 一致（文件头一行写的就是这条约定）；发版提交按历史惯例是一个
只改版本号与 CHANGELOG 的 `chore: release X.Y.Z` 提交，与功能提交分开。

## 源码对照

- 门禁脚本：`scripts/check.sh`、`scripts/release-check.sh`、`scripts/eval.sh`、`scripts/capture_sse.py`
- CI / 发布：`.github/workflows/ci.yml`、`.github/workflows/publish.yml`
- 评测用例：`evals/cases/`（`scripts/eval.py` 负责跑）
- 对应测试：`tests/`（布局与写法见[测试策略](../testing.md)）
