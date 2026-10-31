# ZenAgent 更新提醒设计（2026-10-31）

状态：已批准（2026-10-31，用户确认方案 A）
作者：Lead（与用户讨论后整理）
相关：`docs/research/omp-cli-study/`（omp 机制参考）、`zenagent/cli/tui/app.py`、
`zenagent/cli/tui/widgets/transcript.py`、`zenagent/settings.py`、`tests/conftest.py`

## 1. 意图与为什么

`zenagent-py` 发在 PyPI 上，装了旧版的人不会自己发现新版。目标是让 TUI 在启动后
自己说一句「有新版、这样升」，而不是让用户去看 changelog。

omp 的做法是：启动时并发查版本 → 落后就往 transcript 插一张 notice 卡 → 另配
`omp update` 命令。我们照这个思路，但按本项目约束收窄：

- **只在交互 TUI 提示**（用户已确认）。`-p`、`--json`、REPL、RPC 一律不查、不输出，
  stdout 纯净性是硬约束。
- **版本粒度是整个 distribution**：PyPI 上只有 `zenagent-py` 一个包、一个版本号，
  `[cli]` 只是 extra（多拉 textual/rich/prompt-toolkit），不是第二个版本。核心
  agent loop 与 CLI/TUI 一次升级一起动，所以只比较 `zenagent-py` 的版本。
- 不做 `omp update` 那样的自更新命令；提醒里直接给对应的升级命令。

## 2. 目标 / 非目标 / 反目标

**目标**
1. TUI 启动后，若 PyPI 上 `zenagent-py` 高于本机安装版本，在 transcript（welcome 之后）
   插一行 notice，给出针对当前安装方式的升级命令。
2. 首帧不等网络：24h 内的缓存直接读本地结果；过期才在后台 worker 刷新，刷新结果当场
   可见并写回缓存。
3. 每个新版本只提醒一次；同一次会话最多一条。
4. 任何网络/解析/写盘失败都静默忽略，不影响启动与对话。
5. 可关闭（环境变量）、可测试（缓存路径/查询函数可注入）。

**非目标（本期不做）**
- `-p` / `--json` / REPL / RPC 的更新提示（未来若要做，只走 stderr）。
- `zen update` 自更新、后台常驻轮询、遥测上报。
- extras 依赖（textual 等）的版本跟踪。
- `/settings` 里的可视化开关（v1 只给环境变量）。

**反目标（出现即视为失败）**
- 改变 headless/`--json`/RPC 的任何输出。
- 阻塞启动、卡住 TUI、退出时被网络请求拖住。
- 测试需要真实联网。
- 从源码/可编辑安装跑开发时被天天提醒。
- 每次启动都打 PyPI，或对同一版本反复提醒。

## 3. 约束

- 内核运行时依赖仍只有 `httpx`（新模块只 import 标准库 + httpx，不引 `packaging`）。
- 版本源唯一：`https://pypi.org/pypi/zenagent-py/json` 的 `info.version`；超时 ≤3s；
  UA 形如 `zenagent/<version> update-check`。
- 安装版本取 `importlib.metadata.version("zenagent-py")`，失败则跳过检查（不猜）。
  可编辑/目录安装（`direct_url.json` 的 `dir_info.editable`）跳过。
- 缓存 `~/.zenagent/update.json`（文件 0600、目录 0700），原子写（临时文件 + replace）；
  损坏/不可写按「没查过」处理。`ZENAGENT_UPDATE_CACHE_PATH` 可覆盖路径。
- 网络请求在 app 事件循环的 async worker 里用 `httpx.AsyncClient` 完成（不占线程、
  退出时可取消），UI 追加只在 app 线程上做。

## 4. 方案（用户已选 A）

| | A. 缓存优先 + 后台 worker 刷新（**已选**） | B. 启动即查、结果到了插卡（omp 原样） | C. 只读缓存、刷新留给下次启动 |
|---|---|---|---|
| 首帧 | 不等网络 | 不等网络 | 不等网络 |
| 网络频率 | 24h 最多一次 | 每次启动一次 | 24h 最多一次 |
| 本次可见 | 是（刷新结果当场显示） | 是 | 否（滞后一个启动周期） |
| 线程/生命周期 | Textual worker 托管，可取消 | 需自己防回调时 app 已退出 | 无异步 |
| 测试 | worker 可等，缓存可注入 | 要等网络 fake | 最简单 |

选 A：既拿到「每天最多一次请求」的礼貌性，又不会有 B 的「每次启动都打 PyPI、结果
迟到」和 C 的滞后。omp 的精神（启动并发查、不阻塞、失败静默）保留。

## 5. 行为规格

**缓存文件**

```json
{"checked_at": 1793500000.0, "latest": "0.4.0", "notified": "0.4.0"}
```

字段缺失/类型不对都当空；`latest`/`notified` 是字符串，`checked_at` 是 epoch 秒。

**启动顺序（`ZenAgentApp.on_mount`）**

1. `pending = update_check.pending_notice()`（只读缓存，无网络）：若
   `latest > installed` 且 `notified != latest`，立刻 `append_notice`，并把
   `notified = latest` 写回缓存。
2. `self.run_worker(self._refresh_update_check())`：
   - `now - checked_at < 24h` → 直接返回，不发请求。
   - 否则 `await update_check.refresh()`：
     - 成功：写 `checked_at=now, latest=<PyPI 版本>`；若 `latest > installed` 且
       `notified != latest`，返回该版本。
     - 失败（超时/非 2xx/JSON 异常）：只写 `checked_at=now`（保留上次 `latest`），
       返回 `None`。断网时 24h 内不再重试，避免每次启动白等 3s。
3. 若第 2 步返回候选且本会话还没显示过更新提醒：`append_notice` 并记
   `notified = 候选`。若本会话已显示过（缓存旧值先显示、刷新又发现更新），本次抑制
   显示且**不**写 `notified`，留给下次启动提示。

**文案**（`NoticeRow` 普通一行，dim，不阻塞、可滚走）

```
Update available: v0.4.0 (you have v0.3.0) · uv tool upgrade zenagent-py
```

升级命令按安装方式探测（`sys.prefix` 是运行时依赖，不需要子进程）：

| 安装方式 | 判定 | 命令 |
|---|---|---|
| uv tool | `"/uv/tools/" in sys.prefix` | `uv tool upgrade zenagent-py` |
| pipx | `"/pipx/venvs/" in sys.prefix` | `pipx upgrade zenagent-py` |
| 其他（pip/venv） | 兜底 | `python -m pip install -U "zenagent-py[cli]"` |

uv 会沿用安装时记录的 requirements（含 extras），所以 `uv tool upgrade` 不会丢
`[cli]`；pip 兜底必须写 `[cli]`，否则新版本新增的 TUI 依赖不会补上。

**版本比较**（自写，PEP 440-lite）

- 去首尾空白、允许一个 `v`/`V` 前缀；按 `.` 分段数字比较（缺失段按 0），
  `0.9.0 < 0.10.0`。
- 预发布后缀排序：`dev < a < b < rc < 正式`；`0.4.0rc1 < 0.4.0`。
- 无法解析的段按 0；函数永不抛异常，返回 -1/0/1。
- 够用即可；PyPI 上若出现 build metadata（`+local`）直接忽略。

**关闭 / 测试钩子**

- `ZENAGENT_NO_UPDATE_CHECK=1`（也接受 `true`/`yes`，大小写不敏感）→ 完全不读缓存、
  不发请求、不显示。
- `ZENAGENT_UPDATE_CACHE_PATH` → 覆盖缓存路径（测试用）。
- `tests/conftest.py` 加 autouse fixture 默认 `ZENAGENT_NO_UPDATE_CHECK=1`，既有测试
  零网络；专测再 `monkeypatch.delenv` 并用临时缓存 + fake 刷新函数。

## 6. 模块与改动点

- 新增 `zenagent/update_check.py`（顶层，内核可复用）：版本比较、缓存读写、
  `installed_version()`、`upgrade_command()`、`pending_notice()`、`refresh()`、
  `format_notice()`、`is_disabled()`。
- `zenagent/cli/tui/app.py`：`on_mount` 挂钩 + `_refresh_update_check()` worker +
  `_append_update_notice()`；会话内去重标记。
- `tests/test_update_check.py`（单元）+ `tests/test_tui_update.py`（TUI）。
- `tests/conftest.py`：autouse 关网络 + 隔离缓存路径（沿用 `isolated_settings` 风格）。
- `README.md`：Install 段落后加两三行说明（会检查更新、如何关闭）。
- `docs/guide/tui.md`：启动提示与开关一句话。

## 7. 测试清单

单元（全离线，`httpx.MockTransport` 或注入 fetch）：
1. 版本比较：`0.3.0 < 0.3.1`、`0.9.0 < 0.10.0`、`0.4.0rc1 < 0.4.0`、`0.4.0.dev1 < 0.4.0rc1`、
   `v0.3.0 == 0.3.0`。
2. 缓存：读写 round-trip、损坏 JSON 当空、TTL 判断、原子写后旧文件不残留。
3. `pending_notice`：无缓存/落后/已提醒/已是最新/本地版本缺失各分支。
4. `refresh`：成功且更新（写 latest、返回候选）、成功但落后（写 latest、返回 None）、
   超时/非 2xx/坏 JSON（只更新 checked_at、返回 None）。
5. `upgrade_command`：monkeypatch `sys.prefix` 三态。
6. 守卫：`ZENAGENT_NO_UPDATE_CHECK`、可编辑安装（伪造 `direct_url.json`）、metadata 缺失。

TUI：
7. 临时缓存写入更高版本 → mount 后出现 `Update available: v…` 的 NoticeRow，且只一条。
8. stale 缓存 + monkeypatch `refresh` 返回更高版本 → worker 完成后出现 notice；
   同一次会话不重复。
9. 关闭开关 → 无请求、无 notice。

手工：`uv run zen`（可编辑安装 → 应静默）；伪造 `~/.zenagent/update.json` 看文案；
`ZENAGENT_NO_UPDATE_CHECK=1 uv run zen` 确认关闭。

## 8. 我替用户做的决定（可改）

- 开关只给环境变量，v1 不进 `/settings`（省一轮 UI 改动）。
- 24h TTL；失败也回填 `checked_at`（断网不反复等）。
- 每版本提醒一次（omp 是每次启动都提），并限制一次会话最多一条。
- 提示用普通 `NoticeRow` 一行，不加边框/图标/点击行为。
- 升级命令自动探测，文案里带当前版本号。
- 检查频率/缓存天数都是常量，先不进配置。

## 9. 留给实现者

- `update_check.py` 里函数拆分粒度、类型标注细节。
- 文案断行与 dim 色调是否够醒目（可先按现有 notice 样式）。
- worker 在 `on_mount` 的启动时机（要不要 `call_after_refresh`）按 Textual 实测调整。
