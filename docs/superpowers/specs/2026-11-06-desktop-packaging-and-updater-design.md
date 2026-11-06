# Desktop 打包与自更新（2026-11-06）

状态：待批准（2026-11-06，用户已选「自带 zen」方案 A）
相关：`desktop/src-tauri/src/rpc.rs`、`desktop/src/state/appStore.ts`、
`desktop/src/App.tsx`（ExitScreen）、`zenagent/update_check.py`、
`zenagent/cli/tui/app_parts/update_notice.py`、`scripts/sync-desktop-version.py`、
`.github/workflows/{ci,publish}.yml`、`docs/superpowers/specs/2026-10-31-update-notification-design.md`

## 1. 意图与为什么

desktop 现在只能从源码跑（`pnpm tauri dev`），没有任何可分发的安装包。而它的
Runtime 完全在 Python 侧：`rpc_start` 只是 `Command::new("zen").arg("rpc")` 加
JSONL 转发，模型循环、工具、会话、审批、MCP 全在 `zen rpc` 子进程里。因此"下载即用"
的前提是把 `zen` 打进安装包 —— 只发壳的话，用户双击后只剩一个启动失败。

同时，TUI 已经有一套更新提醒（查 PyPI `zenagent-py`、缓存 1h、一行 notice、只提示
不安装）。GUI 用户拿的是安装包，PyPI 上装了新版对他毫无作用：他需要的是
**GUI 自己也被更新**。而既然 GUI 内嵌了 zen，壳与 zen 必须一起换版本，所以 GUI 的
更新通道只有一个 —— 安装包自己的发布通道（GitHub Release），不是 PyPI。

## 2. 目标 / 非目标 / 反目标

**目标**
- **G1** tag `v*` 产出 6 个安装包，全部内嵌对应架构的 `zen` sidecar：
  macOS arm64 / x86_64（`.app` + `.dmg`）、Windows x64 / arm64（NSIS `.exe`）、
  Linux x64 / arm64（`.AppImage` + `.deb`）。
- **G2** shell 优先使用内嵌 sidecar（版本与 app 严格一致），保留
  `$ZEN_DESKTOP_BIN` → 显式 `bin` → 调试 checkout → PATH 的既有回退链。
- **G3** GUI 自更新：启动后台静默检查（本地缓存优先、TTL 1h、失败静默、可关），
  Settings 提供手动检查，发现新版时一条可关闭 banner + 一键下载安装并重启。
  礼貌性与 `2026-10-31-update-notification-design.md` 对齐（每版本只提示一次）。
- **G4** 版本单一来源仍是 `pyproject.toml`；`sync-desktop-version.py --check` 扩到
  覆盖 sidecar 版本，release workflow 里 tag / pyproject / sidecar 三者必须相等。
- **G5** 不拖累既有 CI：PR 不跑六平台矩阵；release workflow 支持 `workflow_dispatch`
  干跑（只产 artifact，不建 release、不上传任何东西）。

**非目标（本期不做）**
- 把 GUI 发到 PyPI 或 Homebrew cask / winget / AUR。
- Linux deb 的自更新（deb 由包管理器管，只有 AppImage 能自更新）。
- 购买 Apple Developer 证书、Windows EV 证书、macOS 公证。
- MSI 产物（只出 NSIS，见 §7.2）。
- 兼容 zen 与 GUI 版本不一致的组合（内嵌 sidecar 即唯一受支持运行时）。

**反目标（出现即视为失败）**
- 发了安装包，用户打开看到 `failed to start zen rpc: No such file or directory`。
- GUI 内嵌的 zen 版本与 app 版本不同步，用户报告的行为无法复现。
- 更新检查阻塞启动、在离线机器上反复超时、或对同一版本反复提示。
- workflow 因 GitHub artifact 存储配额而失败（`publish.yml` 已踩过这个坑）。
- 六平台矩阵里某个 leg 长期红着没人管（宁可先少一个 leg 并写进文档）。

## 3. 约束（已核实的现状）

- **Tauri v2 sidecar 规则**：`bundle.externalBin` 里写 `binaries/zen`，构建时同目录必须
  存在 `binaries/zen-$TARGET_TRIPLE`（Windows 带 `.exe`）。官方文档直接把 PyInstaller
  列为典型用法。Tauri 把 externalBin 放在**主可执行文件旁**（macOS `Contents/MacOS/`、
  deb/rpm/AppImage 的 `usr/bin/`、Windows 安装目录），所以 `current_exe().parent()`
  是可靠的查找依据。
- **PyInstaller 不能交叉编译**：每个 (os, arch) 必须用原生 runner。可用标签
  （本仓库 public，arm64 runner 免费）：`macos-15`(arm64)、`macos-15-intel`(x86_64，
  `macos-13` 已退役)、`ubuntu-24.04`、`ubuntu-24.04-arm`、`windows-latest`、
  `windows-11-arm`。
- **冻结体积可以很小**：实测 `import zenagent.cli.main` + `zenagent.cli.rpc` 不会拉
  `rich` / `textual` / `prompt_toolkit`；`zenagent/app/__init__.py` 的文档明确该层
  "UI-free"。→ 可以 `--exclude-module` 三个 UI 依赖。
- **但包内有运行必需的数据文件**（初稿写错了，已更正）：`zenagent/catalog.yaml`
  （`providers/catalog.py` 用 `resources.files("zenagent")` 读）、
  `zenagent/prompts/blocks/**/*.md`（`prompts/registry.py` 读）、
  `zenagent/cli/tui/theme.tcss` 与 provider fixtures。PyInstaller 必须显式收
  datas（`collect_data_files("zenagent")` 或等价 hook），否则 onefile 包启动即
  "catalog.yaml not found"。
- **`zen rpc` 默认需要 API key**：`Runtime.bootstrap(..., allow_missing_key=False)`，
  干净机器上无 key 直接 exit 2 并打印
  `zenagent: missing API key for <model> (set <ENV>)`。这条报错要解析出模型名，
  说明 `catalog.yaml` 已被读到 —— 正好可以当"无 key 冒烟检查"。
- **`externalBin` 会在 `cargo check` 阶段就要求文件存在**（`tauri-build` 报
  `resource path ... doesn't exist`）：直接把 `externalBin` 写进基础
  `tauri.conf.json` 会让 `ci.yml` 的 `desktop-rust` 与所有人的 `pnpm tauri dev`
  都必须先产出一个 ~15MB 冻结包。→ externalBin 走只给发布用的 overlay 配置（见 5.5）。
- **Tauri updater 需要的签名与 Apple 证书无关**：它用一个自生成的密钥对签更新包，
  私钥进 CI secret、公钥写进 `tauri.conf.json`，Release 里放 `latest.json` + `.sig`。
  成本为零，可以这一轮就落地。
- **现状没有任何签名/公证凭据**：仓库无 `APPLE_CERTIFICATE` / `TAURI_SIGNING_*` secret，
  `tauri.conf.json` 没有 updater 配置，`bundle.targets` 是 `"all"`。
- **冻结后内置的 PyPI 检查自动失效**：`update_check.installed_version()` 走
  `importlib.metadata.distribution("zenagent-py")`，冻结包里没有 dist metadata →
  返回 `None` → 全程跳过；且 RPC 模式本来就从不调用该模块。所以不会出现
  "TUI 式提示"与"GUI banner"两套打架。
- **`ExitScreen` 已经存在**：`connection === 'exited'` 时 `App.tsx` 渲染 exitCode +
  stderr 末 6 行 + Restart 按钮。运行时缺失不是白屏，而是裸露的
  `failed to start /path/zen rpc: ...` 文本（本轮要把它变成可读的诊断卡片）。

## 4. 方案

### 4.1 运行时怎么带（用户已选 A）

| | A. PyInstaller onefile 当 sidecar（**已选**） | B. 内嵌独立 CPython + 预装 venv | C. 不带，首启引导安装 |
|---|---|---|---|
| 双击即用 | 是 | 是 | 否（要网络 + uv/Python） |
| 安装包增量 | ~15MB/平台 | +60~90MB/平台 | 0 |
| 工具里能用 `python -m ...` | 否（只有冻结的 zen） | 是 | 视用户环境 |
| 构建复杂度 | 一个 spec + 6 个 runner | 解释器 + wheel + venv 三件套 | 最简 |
| 升级路径 | 随 app 一起换 | 可 pip 升级（与 PyPI 通道重复） | 就是 PyPI 通道 |

选 A：它同时满足"双击即用"和"壳与 zen 同版本"，且因为 RPC 路径不 import 三个 UI
依赖，产物很小。B 只在"工具需要真 Python 解释器"时才值得，现在没有这个需求；
C 正是用户明确否掉的（"必须带")。

### 4.2 更新通道怎么走（用户未明说，我的判断）

| | A. `tauri-plugin-updater` 全自动（**已选**） | B. 只提示 + 打开 release 页（TUI 同款） | C. 自己 fetch GitHub API 再下载 |
|---|---|---|---|
| 用户动作 | 点一下 → 下载 → 重启 | 打开浏览器 → 挑 6 个产物之一 → 手动装 | 点一下，但要自己写下载/解包/替换 |
| 会挑错架构吗 | 不会（endpoint 按平台给 URL） | 很可能（arm64/x64 文件名相近） | 不会 |
| 签名校验 | 插件内置（minisign） | 无 | 自己实现 |
| 工作量 | 配置 + 一个 banner | 一个 fetch | 最大 |

选 A。B 的问题在于"内嵌 zen ⇒ 必须整包换"，把 6 个产物丢给用户手挑是设计缺陷；
C 只是把 A 已经做好的事重写一遍。

## 5. 落地设计

### 5.1 sidecar 构建

新增 `scripts/build-sidecar.py`（本地与 CI 同一入口）：

- 只装核心依赖（`uv sync --locked`，**不装 `[cli]` extra**）+ `pyinstaller`。
- 参数：`--name zen`、onefile、`--noconfirm --clean`、
  `--exclude-module textual --exclude-module rich --exclude-module prompt_toolkit`、
  `--collect-data zenagent`（catalog.yaml、prompts/blocks、fixtures、theme.tcss）。
  datas 收全了才允许 onefile 变小，验收标准是下面的冒烟检查而不是体积。
- 输出 `desktop/src-tauri/binaries/zen-<triple>`（Windows `zen-<triple>.exe`），
  triple 由 `rustc -vV` 的 `host:` 行取（CI 里由 matrix 的 target 覆盖同一份）。
- 构建后自检（脚本内，非 CI 专属）：
  1. `zen-<triple> --version` == `pyproject.toml` 的版本；
  2. **无 key 冒烟**：`HOME=<临时空目录>` + 清掉所有 provider key +
     `zen-<triple> rpc --no-mcp`（stdin 给 EOF），断言 exit code 2 且 stderr 匹配
     `missing API key for .+ \(set [A-Z_]+\)`。这条同时证明：二进制能跑、import 完整、
     `catalog.yaml` 读到了、模型解析走到了 bootstrap，而且不联网、不要 key。
  3. 只读 stderr 判定，不依赖 catalog 里哪个 provider 排在前面。
- `--no-mcp` 是必须的（本机 `~/.zenagent/mcp.json` 里有三台服务器，启动会去连）。
- `desktop/src-tauri/binaries/` 加进 `.gitignore`（产物不入库，也避免 stub 混进仓库）。

### 5.2 Rust 侧解析顺序（`rpc.rs::resolve_bin`）

1. `$ZEN_DESKTOP_BIN`；2. 显式 `bin`；3. **`current_exe().parent()/zen`（`.exe`）**；
4. 调试 checkout 的 `.venv/bin/zen`；5. `$HOME/.local/bin`、`/opt/homebrew/bin`、
   `/usr/local/bin`；6. 裸名交给 PATH。

顺序说明（我的判断）：`$ZEN_DESKTOP_BIN` 仍排第一。它是显式覆盖，写它的人知道自己在
覆盖；把“内嵌优先”做成硬规则反而让出问题时没有逃生口。反目标 #2 靠"发布包里根本没有
第二个 zen"来保证，不靠顺序。

配套：`rpc_start` 在 spawn 失败时返回结构化错误
（`{ code: "runtime_missing", bin, os_error }`），而不是拼好的字符串；另外新增
`runtime_probe` 命令，返回当前解析到的路径、是否存在、能否执行、`--version` 输出，
供 ExitScreen 与 Settings 的"诊断"按钮使用。

### 5.3 前端运行时缺失态

`ExitScreen` 分两支：`runtime_missing` 走新卡片（"找不到 zen 运行时"+ 解析到的候选
路径 + 「复制诊断」+ 「重试」）；其他退出仍走现有 exitCode + stderr 尾部 + Restart。
不新增页面，改 `App.tsx` 里已有的两个组件。

### 5.4 GUI 更新检测

依赖：`tauri-plugin-updater`（Rust）、`@tauri-apps/plugin-updater`（JS）。

配置：`plugins.updater = { pubkey: <生成后写入>, endpoints:
["https://github.com/encyc/zen-agent/releases/latest/download/latest.json"] }` 写进基础
`tauri.conf.json`（对 dev 无害）；`bundle.createUpdaterArtifacts` 与 `bundle.externalBin`
一起放到只在发布时用的 overlay（见 5.5）。
Windows 主推 NSIS（只有 NSIS/MSI 中的一种会带更新元数据，选 NSIS）。

行为（照 TUI 的礼貌性）：
- 启动 5s 后查一次；结果写 `localStorage`（`checked_at`、`latest`，TTL 1h）。
- 先读缓存出 banner（不等网络），后台刷新若拿到更新版再更新 banner。
- 每版本只提示一次（`notified`）；失败静默；离线机器等满 TTL 再重试，不每次启动打 GitHub。
- Linux deb 安装：不提供"下载并安装"，banner 退化为"打开 release 页"。

**关闭开关必须是设置项，不能只靠环境变量**（我的判断，也是评审指出的真缺陷）：从
Finder/资源管理器启动的 app 拿到的是 launchd/Explorer 的环境，不是用户 shell profile，
`ZENAGENT_NO_UPDATE_CHECK` 对安装包用户等于不存在（本仓库已有 `login_shell_env()` 就是
为了绕这一点）。所以：Settings → 通用 里一个持久化开关（默认开），环境变量仍然认，
两者任一为关即关。手动"检查更新"不受开关影响（那是用户主动点的）。

**安装时机与失败路径**（我的判断）：
- 有正在跑的一轮（`busy`）或挂着待审批时，banner 的按钮禁用，文案改为
  「完成当前任务后可更新」—— 安装会重启 app，等于打断 run 并丢掉 pending approval。
- 下载中显示进度；安装失败不吞掉，把 banner 切成错误态（错误摘要 + 「打开 release 页」
  兜底），并且不自动重试。
- 更新成功后 app 重启、sidecar 随之换版本；不保留"用旧 zen 跑新壳"的组合。

UI：Settings → About 显示当前版本 + 「检查更新」按钮（手动检查忽略 TTL，显式反馈
"已是最新 / 检查失败"）；发现新版时顶部复用已有 `NoticeBanner`，文案
「vX.Y.Z 可用」+「更新并重启」；zh/en 各一条。

**更新签名密钥**（成本为零，但本轮按 §7.1 取保守值：开关式，默认不启用）：
`tauri signer generate` 产一对密钥，私钥 → repo secret `TAURI_SIGNING_PRIVATE_KEY`
（+ 可选 `_PASSWORD`），公钥 → `tauri.conf.json` 的 `plugins.updater.pubkey`。
公钥一旦发出去就不可变，**私钥丢失等于所有已发 GUI 永远无法更新**，所以必须存在仓库之外
并单独备份。本轮实现要求：secret 缺失时 workflow 不产 updater 产物、release note 标注；
pubkey 为空时前端隐藏"检查更新"入口（而不是让插件去报错）。

### 5.5 `.github/workflows/desktop-release.yml`

- `on: push: tags: ["v*"]` + `workflow_dispatch`（干跑：产 artifact，不建 release）。
- `concurrency: group: desktop-release-${{ github.ref }}`。
- **基础配置不写 `externalBin`**：新增 `desktop/src-tauri/tauri.release.conf.json`
  （overlay），里面只有 `bundle.externalBin: ["binaries/zen"]`；发布 leg 用
  `tauri build --config`（或 tauri-action 的 `args`）合并它。这样 `ci.yml` 的
  `cargo check`、`pnpm tauri dev` 都不需要先产冻结包，G5 成立；updater 的
  `plugins.updater`（pubkey/endpoints）仍在基础配置里，因为它对 dev 无害且发布时必须存在。
  - `createUpdaterArtifacts` 不进 overlay 文件：它在没有 `TAURI_SIGNING_PRIVATE_KEY`
    时会让 `tauri build` 直接失败，而本轮按 §7.1 是不签名发布的。改成 secret 存在时
    追加第二个内联 `--config '{"bundle":{"createUpdaterArtifacts":true}}'`。
  - 副作用要写进 README：本地想产出完整安装包，得先跑 `scripts/build-sidecar.py`
    再带 overlay 构建；不带 overlay 的 `pnpm tauri build` 只产 shell。
- matrix（`fail-fast: false`）：6 组 {`os`, `target`}：
  `macos-15`/`aarch64-apple-darwin`、`macos-15-intel`/`x86_64-apple-darwin`、
  `ubuntu-24.04`/`x86_64-unknown-linux-gnu`、`ubuntu-24.04-arm`/`aarch64-unknown-linux-gnu`、
  `windows-latest`/`x86_64-pc-windows-msvc`、`windows-11-arm`/`aarch64-pc-windows-msvc`。
- 每个 leg：checkout → `setup-uv`（Python 3.12）→ `pnpm` → `scripts/build-sidecar.py`
  → `tauri-apps/tauri-action`（`args: --target <triple>`；tag 才 `tagName`）。
  Linux leg 先 apt 装 webkit2gtk 等（与 `ci.yml` 的 desktop-rust 同清单）。
- **`latest.json` 单独聚合**：六个 leg 并行各自 `includeUpdaterJson` 会互相覆盖
  （最后写完的那个可能只带自己的平台，用户端表现为"永远没有更新"，且不报错）。
  所以矩阵只负责上传产物，末尾一个 `latest` job 用 release 上已有的 `.sig` 资产重建
  一份完整 `latest.json` 再覆盖上传。这是与初稿不同的地方，故意改的。
- 与 `publish.yml` 并存：同一个 tag 既上 PyPI（CLI 用户）也上 GitHub Release（GUI 用户）；
  两者都不走 `upload-artifact` 的中转（`publish.yml` 的配额教训），tauri-action 直接传 release。
- 干跑与真发分开：dispatch 时用 `upload-artifact` 收本地安装包，release 步骤加
  `if: startsWith(github.ref, 'refs/tags/v')`；`releaseDraft: false`，
  否则 `releases/latest/download/latest.json` 这个匿名 URL 不生效。
- 附 `sha256sum` 清单进 release body。

### 5.6 版本对齐

`sync-desktop-version.py --check` 增加一项：`desktop/src-tauri/binaries/` 下存在的
sidecar 名字里的版本无关（文件名不带版本），但 release workflow 在构建 sidecar 后断言
`zen --version == pyproject 版本 == tauri.conf 版本`；`scripts/check.sh` 不新增 Python 侧工作。

### 5.7 文档

`desktop/README.md` 加 "Install" 段（下载哪个、macOS 首次打开需要右键→打开或
`xattr -dr com.apple.quarantine`、更新策略、"没有 GUI 就没有 Runtime 例外"），
`CHANGELOG.md` Unreleased 记一条。

## 6. 留给实现者的判断与风险

- **数据文件是最大的单点风险**：`--collect-data zenagent` 收不全（例如漏了
  `prompts/blocks` 子目录）时，包能启动、能打印 `--version`，但一到真正跑 prompt 才炸。
  所以 5.1 的无 key 冒烟是发布阻断项，不是"nice to have"。
- **`windows-11-arm` 是新镜像**：uv 装 CPython 3.12（aarch64-pc-windows-msvc）、
  PyInstaller、WebView2 在这条 leg 上可能踩坑。先按会红来写，不预设
  `continue-on-error`；若确实阻塞发版，允许临时降级为"Windows arm64 暂缺，写进
  release note"，但要写进文档而不是悄悄跳过。
- **macOS 未公证**：updater 自己验签，不依赖 Apple 签名，理论上能更新未签名的 `.app`；
  需在真机验证一次（手工验证项）。
- **PyInstaller onefile 启动开销**：首次启动要解包到临时目录（几百 ms~2s）。若体验不可
  接受，退路是把 onedir 产物放进 `bundle.resources` 并用 `resource_dir()` 解析 —— 这会
  改动 5.1/5.2，属于实现者可自行决定的范围，但要先量一次真实耗时。
- **macOS arm64 至少需要 ad-hoc 签名**：Tauri 默认会做，不需要证书；如果 `codesign`
  在 CI 上因 sidecar 而失败，实现者可以在构建后对 `Contents/MacOS/zen` 单独 ad-hoc 签一次。
- **模型/工具对 Python 的隐含依赖**：如果将来某个工具需要真解释器，方案 A 会露出短板
  （届时再评估方案 B）。本轮不做预防性设计。
- **验证的现实**：CI 只能证明"六个 leg 都构建成功"，证明不了"装上去能用"。本机只
  有 macOS arm64 可以真跑；Windows / Linux / macOS x86_64 的首次真机验证只能靠用户
  或首批反馈。因此 v1 的建议是：先 `workflow_dispatch` 干跑一遍拿安装包，手工装一台
  确认，再打 tag。

## 7. 本轮假设（用户未在线回答，取保守值；均可单点翻转）

1. **更新签名密钥：这一轮不生成、不入库。** 我改成了"开关式"设计：release workflow 在
   `TAURI_SIGNING_PRIVATE_KEY` 缺失时照常出安装包，但跳过
   `createUpdaterArtifacts`、不产 `latest.json`、在 release note 里标一句"本版未启用
   自更新"；前端在 pubkey 为空时隐藏检查更新入口。用户想启用时执行一次：
   `pnpm tauri signer generate` → 私钥存 1Password 等仓库外位置 →
   `gh secret set TAURI_SIGNING_PRIVATE_KEY` → 公钥填进 `plugins.updater.pubkey`，
   下个 tag 起自更新生效。这样不会把"不可逆的公钥承诺"替用户做掉。
2. **Windows 只出 NSIS（`.exe`），弃 MSI**：一个安装器、一条更新路径，没有歧义。
   要企业分发再加 MSI 时，得同时做"安装来源检测 + banner 退化为打开下载页"。
3. **发布流程加一道真机闸**：CI 绿不等于能用。先 `workflow_dispatch` 干跑拿全部产物 →
   用户在本机装 macOS arm64 与 Windows/Linux 各一台确认 → 再打 tag。
   若跳过这道闸，release note 里会写明"未经真机验证"。
