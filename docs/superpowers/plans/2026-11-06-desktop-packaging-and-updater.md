# Desktop 打包与自更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 desktop 变成可分发的六个安装包（macOS/Windows/Linux × x86_64/arm64），每个包内嵌同架构的冻结 `zen` 运行时，并让 GUI 能自查并安装新版本。

**Architecture:** PyInstaller onefile 冻结 `zen`，按 `-$TARGET_TRIPLE` 命名放进 `desktop/src-tauri/binaries/`，由只在发布时使用的 overlay 配置注入 `bundle.externalBin`；Rust 壳在 `current_exe()` 同级查找该二进制并用结构化错误报告缺失；自更新走 `tauri-plugin-updater` + GitHub Release 的 `latest.json`。

**Tech Stack:** Python 3.12 + PyInstaller 6 + uv；Rust（Tauri 2，`tauri-plugin-updater`）；React 18 + TypeScript + zustand；GitHub Actions（`tauri-apps/tauri-action`）。

**Spec:** `docs/superpowers/specs/2026-11-06-desktop-packaging-and-updater-design.md`

## Global Constraints

- 版本唯一来源是 `pyproject.toml` 的 `[project] version`；sidecar `--version`、`tauri.conf.json`、`package.json`、`Cargo.toml`、`Cargo.lock` 必须与它相等。
- sidecar 只装核心依赖，**不装 `[cli]` extra**；构建时必须 `--collect-data zenagent`，并排除 `textual`、`rich`、`prompt_toolkit`。
- sidecar 产物文件名固定为 `zen-<triple>`（Windows 加 `.exe`），落在 `desktop/src-tauri/binaries/`，且该目录不入库。
- `bundle.externalBin` 与 `bundle.createUpdaterArtifacts` **只能**出现在 `desktop/src-tauri/tauri.release.conf.json`，基础 `tauri.conf.json` 不得出现。
- 矩阵固定六组：`macos-15`/`aarch64-apple-darwin`、`macos-15-intel`/`x86_64-apple-darwin`、`ubuntu-24.04`/`x86_64-unknown-linux-gnu`、`ubuntu-24.04-arm`/`aarch64-unknown-linux-gnu`、`windows-latest`/`x86_64-pc-windows-msvc`、`windows-11-arm`/`aarch64-pc-windows-msvc`。Windows 只出 NSIS。
- 更新检查：TTL 1h；失败静默；每版本只提示一次；开关默认开，设置项与 `ZENAGENT_NO_UPDATE_CHECK` 任一为关即关（手动检查不受开关影响）。
- 更新签名密钥本轮不生成；secret 缺失时 workflow 照常出安装包但不产 updater 产物。
- 交付语言：新增 UI 文案 zh/en 两条都加；Python 用 pytest（`uv run pytest -q`），TS 用 `node --test`，Rust 用 `cargo test --manifest-path desktop/src-tauri/Cargo.toml`。
- 门禁：`bash scripts/check.sh`（Python）→ `cd desktop && pnpm test && pnpm build` → `cargo test`。

## Review Focus

1. 冻结包里 `zenagent/prompts/blocks/**` 缺失：包能启动、`--version` 正常，但一跑 prompt 就崩。期望：构建脚本的无 key 冒烟在 CI 上直接失败（Task 1）。
2. `current_exe()` 同级存在 `zen` 但不可执行或架构不符：期望 GUI 显示"找不到可用运行时"而不是裸 EACCES 文本（Task 3）。
3. 用户手动装了更新版本，缓存里仍留着旧的 `latest`：期望不再提示（Task 4 的 `compareVersions` + `bannerFor`）。
4. 正在跑一轮、或挂着待审批时点"更新并重启"：期望按钮禁用并说明原因，不打断 run（Task 4、Task 5）。
5. 用户把设置里的更新开关关掉：期望连启动检查都不发请求，环境变量不存在也不复活（Task 4）。

---

### Task 1: 冻结 sidecar 的构建脚本

**Files:**
- Create: `scripts/zen_sidecar_entry.py`
- Create: `scripts/build-sidecar.py`
- Test: `tests/test_sidecar_build.py`
- Modify: `.gitignore`（加一行 `desktop/src-tauri/binaries/`）

**Interfaces:**
- Produces（`scripts/build-sidecar.py`，测试用 `importlib.util.spec_from_file_location` 按路径加载，参照 `tests/test_evals_runner.py:76-82`）:
  - `def sidecar_name(triple: str) -> str` — 三元组含 `-windows-` 时返回 `zen-<triple>.exe`，否则 `zen-<triple>`
  - `def host_triple(rustc_output: str) -> str` — 从 `rustc -vV` 文本取 `host: ...`，缺失时 `raise ValueError`
  - `def project_version(pyproject: Path) -> str`
  - `def smoke_env(base: Mapping[str, str], home: Path) -> dict[str, str]` — 去掉所有以 `_API_KEY` 结尾的键，设 `HOME`、`USERPROFILE`、`ZENAGENT_UPDATE_CACHE_PATH=<home>/update.json`
  - `def missing_key_ok(stderr: str) -> bool` — 匹配 `missing API key for .+ \(set [A-Z0-9_]+\)`
  - `def build(out_dir: Path, triple: str, project_root: Path) -> Path`
  - `def smoke(binary: Path, version: str, home: Path) -> None` — 失败时 `raise SystemExit`
  - `def main(argv: list[str]) -> int` — 参数 `--out-dir`（默认 `desktop/src-tauri/binaries`）、`--target`、`--skip-smoke`
- Consumes: `zenagent.cli.main:main`（entry 里 `raise SystemExit(main())`）

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_sidecar_build.py
def test_sidecar_name_carries_the_triple_and_the_windows_suffix():
    assert sidecar_name("aarch64-apple-darwin") == "zen-aarch64-apple-darwin"
    assert sidecar_name("x86_64-pc-windows-msvc") == "zen-x86_64-pc-windows-msvc.exe"

def test_host_triple_reads_the_rustc_host_line():
    output = "rustc 1.85.0\ntarget: x86_64-unknown-linux-gnu\nhost: x86_64-unknown-linux-gnu\n"
    assert host_triple(output) == "x86_64-unknown-linux-gnu"

def test_host_triple_rejects_output_without_a_host_line():
    with pytest.raises(ValueError):
        host_triple("rustc 1.85.0\n")

def test_smoke_env_drops_provider_keys_and_pins_home(tmp_path):
    env = smoke_env({"DEEPSEEK_API_KEY": "x", "PATH": "/bin"}, tmp_path)
    assert "DEEPSEEK_API_KEY" not in env
    assert env["PATH"] == "/bin"
    assert env["HOME"] == str(tmp_path)

def test_missing_key_ok_accepts_the_bootstrap_error_only():
    assert missing_key_ok("zenagent: missing API key for deepseek-flash (set DEEPSEEK_API_KEY)\n")
    assert not missing_key_ok("Traceback (most recent call last):\nModuleNotFoundError: rich\n")
    assert not missing_key_ok("")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_sidecar_build.py -q`
Expected: FAIL（模块加载后 `NameError`/`AttributeError`，因为脚本还不存在）

- [ ] **Step 3: 实现脚本**

`scripts/zen_sidecar_entry.py`：`from zenagent.cli.main import main` + `raise SystemExit(main())`。

`scripts/build-sidecar.py` 的构建命令（其余细节由实现者补，但参数必须一致）：

```python
["uv", "run", "--with", "pyinstaller>=6.10", "python", "-m", "PyInstaller",
 "--noconfirm", "--clean", "--onefile", "--name", "zen",
 "--collect-data", "zenagent",
 "--exclude-module", "textual", "--exclude-module", "rich",
 "--exclude-module", "prompt_toolkit",
 "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
 "--specpath", str(work), str(entry_script)]
```

（若按 Step 3 的隔离 venv 建，则改用 `<work>/.venv/bin/python -m PyInstaller ...`，参数完全相同。）

产出从 `<work>/dist/zen(.exe)` 复制/重命名到 `<out_dir>/<sidecar_name(triple)>`。构建环境用**隔离 venv 且非 editable 安装**（`uv venv --python 3.12 <work>/.venv` + `uv pip install --python <work>/.venv .` + `uv pip install --python <work>/.venv "pyinstaller>=6.10"`），否则 editable 安装会让 PyInstaller 的模块收集走 `.pth` 而漏子模块。`smoke()` 用 `smoke_env` 起 `[binary, "rpc", "--no-mcp"]`，stdin 立即关闭，断言 `returncode == 2` 且 `missing_key_ok(stderr)`；另跑 `[binary, "--version"]` 断言输出含 `project_version()`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_sidecar_build.py -q`
Expected: 5 passed

- [ ] **Step 5: 本机真跑一次构建（macOS arm64）**

Run: `uv run python scripts/build-sidecar.py`
Expected: 产出 `desktop/src-tauri/binaries/zen-aarch64-apple-darwin`，冒烟通过，打印体积（<40MB）

- [ ] **Step 6: Commit**

```bash
git add scripts/zen_sidecar_entry.py scripts/build-sidecar.py tests/test_sidecar_build.py .gitignore
git commit -m "feat(scripts): freeze the zen runtime into a sidecar binary"
```

---

### Task 2: 壳优先使用同目录 sidecar

**Files:**
- Modify: `desktop/src-tauri/src/rpc.rs:126-160`（`dev_checkout_bin`/`resolve_bin`）
- Modify: `.github/workflows/ci.yml:59`（`cargo check` 换成 `cargo check` + `cargo test`）

**Interfaces:**
- Produces:
  - `pub fn candidate_paths(bin: &str, exe_dir: Option<&Path>, home: Option<&str>) -> Vec<String>` — 按顺序：`exe_dir/<bin>`（若 `exe_dir` 有值）、`<home>/.local/bin/<bin>`（若 `home` 有值）、`/opt/homebrew/bin/<bin>`、`/usr/local/bin/<bin>`
  - `fn exe_sibling_name() -> &'static str` — `"zen.exe"`（windows）或 `"zen"`
- Consumes: 现有 `dev_checkout_bin()`（`debug_assertions` 才有）

- [ ] **Step 1: 写失败的单元测试**（`rpc.rs` 末尾 `#[cfg(test)] mod tests`）

```rust
#[test]
fn candidate_paths_puts_the_executable_sibling_first() {
    let paths = candidate_paths("zen", Some(Path::new("/Applications/ZenAgent.app/Contents/MacOS")), Some("/Users/u"));
    assert_eq!(paths[0], "/Applications/ZenAgent.app/Contents/MacOS/zen");
    assert_eq!(paths[1], "/Users/u/.local/bin/zen");
    assert_eq!(paths[2], "/opt/homebrew/bin/zen");
    assert_eq!(paths[3], "/usr/local/bin/zen");
}

#[test]
fn candidate_paths_skips_what_it_does_not_know() {
    assert_eq!(candidate_paths("zen", None, None), vec!["/opt/homebrew/bin/zen", "/usr/local/bin/zen"]);
}

#[test]
fn candidate_paths_keeps_the_windows_extension() {
    let paths = candidate_paths("zen.exe", Some(Path::new("C:\\Zen")), None);
    assert!(paths[0].contains("Zen"));
    assert!(paths[0].ends_with("zen.exe"));
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml candidate_paths`
Expected: FAIL（`candidate_paths` 未定义）

- [ ] **Step 3: 实现**

`candidate_paths` 只做拼接与顺序，不碰文件系统；`resolve_bin` 变成：`$ZEN_DESKTOP_BIN` → 含 `/` 的显式路径直返 → `dev_checkout_bin()` **之后**才轮到 `candidate_paths` 中的 exe 同级候选……注意：spec §5.2 的顺序是 exe 同级(3) 在 dev checkout(4) 之前，照 spec 实现；`candidate_paths` 返回完整候选列表，`resolve_bin` 用 `current_exe().parent()` 与 `HOME` 生成参数后取第一个 `is_file()` 命中的项，全不命中则返回裸 `bin`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml`
Expected: 3 passed（外加既有的 0 个）

- [ ] **Step 5: 让 CI 跑这些测试**

`ci.yml` 的 `desktop-rust` job 在 `cargo check` 之后加一步：

```yaml
      - run: cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

- [ ] **Step 6: Commit**

```bash
git add desktop/src-tauri/src/rpc.rs .github/workflows/ci.yml
git commit -m "feat(desktop): run the bundled zen sidecar before PATH lookups"
```

---

### Task 3: 运行时缺失时显示诊断而不是裸错误

**Files:**
- Modify: `desktop/src-tauri/src/rpc.rs`（`StartError`、`rpc_start` 返回类型、新增 `runtime_probe`）
- Modify: `desktop/src-tauri/src/lib.rs`（注册 `runtime_probe`）
- Modify: `desktop/src/rpc/client.ts:25-47`（`start` 抛结构化错误）
- Create: `desktop/src/state/startFailure.ts`
- Modify: `desktop/src/state/appStore.ts`（新增 `startFailure` 字段，`connect` 的 catch 里写入）
- Modify: `desktop/src/App.tsx:249-265`（`ExitScreen` 分支）
- Test: `desktop/tests/startFailure.test.ts`

**Interfaces:**
- Produces:
  - Rust: `pub struct StartError { pub code: &'static str, pub bin: String, pub os_error: String }`（`code` 恒为 `"runtime_missing"`）；`pub struct RuntimeProbe { pub resolved: Option<String>, pub exists: bool, pub version: Option<String> }`；`#[tauri::command] pub fn runtime_probe() -> RuntimeProbe`
  - TS: `export interface StartFailure { code: string; bin: string; osError: string }`；`export function startFailureOf(error: unknown): StartFailure | null`；`export function isRuntimeMissing(failure: StartFailure | null): boolean`；`export function probeHint(probe: { resolved: string | null; exists: boolean }): string`
- Consumes: Task 2 的 `candidate_paths`/`resolve_bin`

- [ ] **Step 1: 写失败的测试**

```typescript
// desktop/tests/startFailure.test.ts
test('a structured start error keeps its code, bin and os error', () => {
  const failure = startFailureOf({ code: 'runtime_missing', bin: '/app/zen', osError: 'No such file or directory (os error 2)' })
  assert.deepEqual(failure, { code: 'runtime_missing', bin: '/app/zen', osError: 'No such file or directory (os error 2)' })
})

test('a plain string rejection is not treated as a structured failure', () => {
  assert.equal(startFailureOf('failed to start zen rpc: No such file or directory'), null)
})

test('only the runtime_missing code renders the runtime card', () => {
  assert.equal(isRuntimeMissing({ code: 'runtime_missing', bin: '/app/zen', osError: '' }), true)
  assert.equal(isRuntimeMissing({ code: 'other', bin: '/app/zen', osError: '' }), false)
  assert.equal(isRuntimeMissing(null), false)
})

test('the probe hint names the resolved path only when it exists', () => {
  assert.equal(probeHint({ resolved: '/app/zen', exists: true }), '/app/zen')
  assert.equal(probeHint({ resolved: null, exists: false }), '')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && node --test tests/startFailure.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

Rust `rpc_start` 的签名改为 `Result<StartReport, StartError>`，`spawn()` 失败时返回 `StartError { code: "runtime_missing", bin, os_error: error.to_string() }`。`runtime_probe` 用 `resolve_bin("zen")` + `Path::is_file()` + 起一次 `--version`（超时 2s，失败即 `None`）。`client.ts` 的 `start` 把 reject 值转成 `StartFailure` 抛出（保留字符串 reject 的旧行为）。`appStore.connect` 的 catch 里 `set({ startFailure })`，成功时清空。`ExitScreen` 在 `startFailure?.code === 'runtime_missing'` 时渲染"找不到 zen 运行时"卡片：主标题、`bin`、`osError`、「复制诊断」（`navigator.clipboard.writeText`）、「重试」；否则维持现有 exitCode + stderr 尾部。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd desktop && node --test tests/startFailure.test.ts && pnpm build`
Expected: 4 passed，`tsc --noEmit` 与 vite 构建均通过

- [ ] **Step 5: Commit**

```bash
git add desktop/src-tauri/src/rpc.rs desktop/src-tauri/src/lib.rs desktop/src/rpc/client.ts \
        desktop/src/state/startFailure.ts desktop/src/state/appStore.ts desktop/src/App.tsx \
        desktop/tests/startFailure.test.ts
git commit -m "feat(desktop): show a runtime card when the bundled zen is missing"
```

---

### Task 4: 更新策略（纯函数）与设置开关

**Files:**
- Create: `desktop/src/state/update.ts`
- Modify: `desktop/src/state/stores.ts:283-350`（`SettingsState` 加 `updateChecks`/`setUpdateChecks`）
- Modify: `desktop/src-tauri/src/rpc.rs`（`#[tauri::command] pub fn updates_disabled_by_env() -> bool`）
- Modify: `desktop/src-tauri/src/lib.rs`（注册）
- Test: `desktop/tests/update.test.ts`

**Interfaces:**
- Produces:
  - `export const UPDATE_TTL_MS = 3_600_000`
  - `export interface UpdateCache { checkedAt: number; latest: string; notified: string | null }`
  - `export function compareVersions(a: string, b: string): -1 | 0 | 1`（只比 `\d+` 段，缺位补 0，忽略前缀 `v`）
  - `export function shouldOffer(cache: UpdateCache, current: string): boolean`
  - `export function cacheIsFresh(cache: UpdateCache, now: number): boolean`
  - `export type UpdatePhase = 'idle' | 'checking' | 'available' | 'installing' | 'failed'`
  - `export type UpdateBanner = { kind: 'hidden' } | { kind: 'available'; version: string } | { kind: 'blocked'; version: string } | { kind: 'installing'; version: string } | { kind: 'failed'; message: string }`
  - `export function bannerFor(input: { cache: UpdateCache | null; current: string; enabled: boolean; busy: boolean; pendingApprovals: number; phase: UpdatePhase }): UpdateBanner`
- Consumes: 无（Task 5 使用这些函数）

- [ ] **Step 1: 写失败的测试**

```typescript
// desktop/tests/update.test.ts
const cache = (latest: string, notified: string | null = null, checkedAt = 0): UpdateCache =>
  ({ checkedAt, latest, notified })

test('versions compare numerically, not as strings', () => {
  assert.equal(compareVersions('0.10.0', '0.9.9'), 1)
  assert.equal(compareVersions('v0.8.1', '0.8.1'), 0)
  assert.equal(compareVersions('0.8', '0.8.0'), 0)
  assert.equal(compareVersions('0.7.9', '0.8.0'), -1)
})

test('a cache entry equal to the running version is never offered', () => {
  assert.equal(shouldOffer(cache('0.8.1'), '0.8.1'), false)
  assert.equal(shouldOffer(cache('0.9.0'), '0.8.1'), true)
  assert.equal(shouldOffer(cache('0.9.0', '0.9.0'), '0.8.1'), false)
})

test('the cache goes stale after an hour', () => {
  assert.equal(cacheIsFresh(cache('0.9.0', null, 1_000), 1_000 + UPDATE_TTL_MS - 1), true)
  assert.equal(cacheIsFresh(cache('0.9.0', null, 1_000), 1_000 + UPDATE_TTL_MS), false)
})

test('the banner waits for an idle app before offering to restart', () => {
  const input = { cache: cache('0.9.0'), current: '0.8.1', enabled: true, busy: true, pendingApprovals: 0, phase: 'available' as const }
  assert.deepEqual(bannerFor(input), { kind: 'blocked', version: '0.9.0' })
})

test('a disabled switch hides everything except an explicit install in flight', () => {
  const base = { cache: cache('0.9.0'), current: '0.8.1', busy: false, pendingApprovals: 0 }
  assert.deepEqual(bannerFor({ ...base, enabled: false, phase: 'available' }), { kind: 'hidden' })
  assert.deepEqual(bannerFor({ ...base, enabled: true, phase: 'available' }), { kind: 'available', version: '0.9.0' })
  assert.deepEqual(bannerFor({ ...base, enabled: true, phase: 'failed' }), { kind: 'failed', message: 'update.failed' })
})

test('a stale cache does not resurrect a version the user already installed', () => {
  assert.deepEqual(
    bannerFor({ cache: cache('0.8.1'), current: '0.8.1', enabled: true, busy: false, pendingApprovals: 0, phase: 'available' }),
    { kind: 'hidden' },
  )
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && node --test tests/update.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`update.ts` 按上面的签名实现纯函数（不 import Tauri、不读 localStorage）。`stores.ts` 的 `SettingsState` 加 `updateChecks: boolean`（`load('zen.update.checks', true)`）与 `setUpdateChecks`（`persist('zen.update.checks', value)`）。Rust 加 `updates_disabled_by_env()`：读 `ZENAGENT_NO_UPDATE_CHECK`，`1/true/yes` 为真（与 `zenagent/update_check.py` 的 `is_disabled` 同一语义）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd desktop && node --test tests/update.test.ts && pnpm test`
Expected: 6 passed，整套 `node --test tests/*.test.ts` 全绿

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/update.ts desktop/src/state/stores.ts desktop/tests/update.test.ts \
        desktop/src-tauri/src/rpc.rs desktop/src-tauri/src/lib.rs
git commit -m "feat(desktop): decide update banners from a cached, switchable check"
```

---

### Task 5: GUI 自更新的界面与安装

**Files:**
- Modify: `desktop/package.json`（`@tauri-apps/plugin-updater`）
- Modify: `desktop/src-tauri/Cargo.toml`（`tauri-plugin-updater = "2"`）
- Modify: `desktop/src-tauri/src/lib.rs`（`tauri_plugin_updater::Builder::new().build()`）
- Modify: `desktop/src-tauri/capabilities/default.json`（`"updater:default"`）
- Modify: `desktop/src-tauri/tauri.conf.json`（`plugins.updater.endpoints`；`pubkey` 留空字符串）
- Create: `desktop/src/state/updateStore.ts`
- Create: `desktop/src/ui/UpdateBanner.tsx`
- Modify: `desktop/src/App.tsx`（在 `<NoticeBanner />` 旁挂载）
- Modify: `desktop/src/ui/pages/SettingsPage.tsx:442-450`（About 里加版本行下面的检查按钮；`general` tab 加开关）
- Modify: `desktop/src/i18n/locale.ts`（zh/en 各加 9 个键）
- Test: `desktop/tests/updateStore.test.ts`

**Interfaces:**
- Produces:
  - `export const useUpdateStore = create<UpdateState>(...)`，字段 `{ cache: UpdateCache | null; phase: UpdatePhase; message: string | null; start(): Promise<void>; checkNow(): Promise<'latest' | 'available' | 'failed'>; install(): Promise<void>; dismiss(): void }`
  - `export function bootUpdateCheck(deps: { current: string; settings: { updateChecks: boolean }; envDisabled: boolean; busy: boolean; pendingApprovals: number; cache?: UpdateCache | null; now?: number }): 'skip' | 'cache' | 'network'`
  - i18n 键：`update.available`、`update.blocked`、`update.install`、`update.installing`、`update.failed`、`update.latest`、`update.check`、`update.checkFailed`、`update.toggleHint`
- Consumes: Task 4 的 `bannerFor`/`shouldOffer`/`cacheIsFresh`/`UPDATE_TTL_MS`、`__APP_VERSION__`

- [ ] **Step 1: 写失败的测试**

```typescript
// desktop/tests/updateStore.test.ts
const base = { current: '0.8.1', settings: { updateChecks: true }, envDisabled: false, busy: false, pendingApprovals: 0 }

test('the env switch and the settings switch both silence the boot check', () => {
  assert.equal(bootUpdateCheck({ ...base, envDisabled: true }), 'skip')
  assert.equal(bootUpdateCheck({ ...base, settings: { updateChecks: false } }), 'skip')
})

test('a fresh cache answers without touching the network', () => {
  assert.equal(bootUpdateCheck({ ...base, cache: { checkedAt: Date.now(), latest: '0.9.0', notified: null } }), 'cache')
})

test('a stale cache triggers one network check', () => {
  assert.equal(bootUpdateCheck({ ...base, cache: { checkedAt: 0, latest: '0.9.0', notified: null } }), 'network')
})

test('a missing cache triggers one network check', () => {
  assert.equal(bootUpdateCheck(base), 'network')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && node --test tests/updateStore.test.ts`
Expected: FAIL（`bootUpdateCheck` 未定义）

- [ ] **Step 3: 实现**

`bootUpdateCheck` 是 `updateStore.ts` 里的纯函数（不 import 插件），`UpdateState.start()` 调它决定 skip/cache/network，网络路径才 `import('@tauri-apps/plugin-updater').then(m => m.check())`；结果写 `localStorage` 键 `zen.update.cache`，`notified` 在 banner 首次渲染时写。`install()` 调 `update.downloadAndInstall(onEvent)` 后 `relaunch()`；`busy` 或 `pendingApprovals > 0` 时 `UpdateBanner` 的按钮禁用并显示 `update.blocked`。`UpdateBanner` 用 `NoticeBanner.module.css` 的类名 + 自己的按钮，`data-kind` 复用现有样式钩子。

- [ ] **Step 4: 跑测试与构建**

Run: `cd desktop && node --test tests/updateStore.test.ts && pnpm test && pnpm build && cargo test --manifest-path src-tauri/Cargo.toml`
Expected: 4 passed + 全套绿 + 构建通过

- [ ] **Step 5: Commit**

```bash
git add desktop/package.json desktop/pnpm-lock.yaml desktop/src-tauri/Cargo.toml \
        desktop/src-tauri/Cargo.lock desktop/src-tauri/src/lib.rs \
        desktop/src-tauri/capabilities/default.json desktop/src-tauri/tauri.conf.json \
        desktop/src/state/updateStore.ts desktop/src/ui/UpdateBanner.tsx desktop/src/App.tsx \
        desktop/src/ui/pages/SettingsPage.tsx desktop/src/i18n/locale.ts desktop/tests/updateStore.test.ts
git commit -m "feat(desktop): offer in-app updates behind a settings switch"
```

---

### Task 6: 六平台发布流水线

**Files:**
- Create: `desktop/src-tauri/tauri.release.conf.json`
- Create: `.github/workflows/desktop-release.yml`
- Modify: `desktop/README.md`（Install 段）
- Modify: `CHANGELOG.md`（Unreleased）

**Interfaces:**
- Produces: 名为 `desktop-release` 的 workflow；overlay 文件内容是 `{"bundle": {"externalBin": ["binaries/zen"]}}`
- Consumes: `scripts/build-sidecar.py`（Task 1）、`tauri.conf.json`（Task 5 的 updater endpoints）

**与 spec 的一处偏差（有意）**：`createUpdaterArtifacts` 不写进 overlay 文件。它在没有
`TAURI_SIGNING_PRIVATE_KEY` 时会让 `tauri build` 直接失败，而本轮按 spec §7.1 是不签名
发布的。所以只在 secret 存在时用第二个 `--config`（内联 JSON）打开它，见 Step 2。

- [ ] **Step 1: 写 overlay 配置并确认基础配置干净**

`desktop/src-tauri/tauri.release.conf.json` 只含 `bundle.externalBin`。检查：
`grep -c externalBin desktop/src-tauri/tauri.conf.json` 必须是 0。

- [ ] **Step 2: 写 workflow**

```yaml
name: desktop-release
on:
  push:
    tags: ["v*"]
  workflow_dispatch:
concurrency:
  group: desktop-release-${{ github.ref }}
  cancel-in-progress: false
jobs:
  build:
    name: build (${{ matrix.target }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - { os: macos-15, target: aarch64-apple-darwin }
          - { os: macos-15-intel, target: x86_64-apple-darwin }
          - { os: ubuntu-24.04, target: x86_64-unknown-linux-gnu }
          - { os: ubuntu-24.04-arm, target: aarch64-unknown-linux-gnu }
          - { os: windows-latest, target: x86_64-pc-windows-msvc }
          - { os: windows-11-arm, target: aarch64-pc-windows-msvc }
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with: { python-version: "3.12", enable-cache: true }
      - uses: dtolnay/rust-toolchain@stable
        with: { targets: "${{ matrix.target }}" }
      - uses: pnpm/action-setup@v4
        with: { version: 10 }
      - uses: actions/setup-node@v4
        with: { node-version: 24, cache: pnpm, cache-dependency-path: desktop/pnpm-lock.yaml }
      - name: Install Tauri Linux dependencies
        if: startsWith(matrix.os, 'ubuntu')
        run: |
          sudo apt-get update
          sudo apt-get install -y libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev patchelf
      - run: pnpm install --frozen-lockfile
        working-directory: desktop
      - run: uv run python scripts/build-sidecar.py --target ${{ matrix.target }}
      - name: Note whether self-update is enabled
        id: signer
        shell: bash
        env:
          KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
        run: |
          if [ -n "$KEY" ]; then
            echo "enabled=1" >> "$GITHUB_OUTPUT"
            echo "args=--config src-tauri/tauri.release.conf.json --config {\"bundle\":{\"createUpdaterArtifacts\":true}}" >> "$GITHUB_OUTPUT"
          else
            echo "enabled=" >> "$GITHUB_OUTPUT"
            echo "args=--config src-tauri/tauri.release.conf.json" >> "$GITHUB_OUTPUT"
          fi
      - uses: tauri-apps/tauri-action@v0
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        with:
          projectPath: desktop
          tauriScript: pnpm tauri
          tagName: ${{ startsWith(github.ref, 'refs/tags/v') && github.ref_name || '' }}
          releaseName: ${{ startsWith(github.ref, 'refs/tags/v') && format('ZenAgent {0}', github.ref_name) || '' }}
          releaseDraft: false
          prerelease: false
          includeUpdaterJson: false
          args: --target ${{ matrix.target }} ${{ steps.signer.outputs.args }} --bundles ${{ startsWith(matrix.os, 'windows') && 'nsis' || (startsWith(matrix.os, 'macos') && 'app,dmg' || 'appimage,deb') }}
      - name: Tag must match the project version
        if: startsWith(github.ref, 'refs/tags/v')
        shell: bash
        run: |
          version="$(sed -n 's/^version = "\([^"]*\)"$/\1/p' pyproject.toml | head -n 1)"
          test "v${version}" = "${GITHUB_REF_NAME}" || { echo "tag ${GITHUB_REF_NAME} != pyproject ${version}"; exit 1; }
      - uses: actions/upload-artifact@v4
        if: ${{ !startsWith(github.ref, 'refs/tags/v') }}
        with:
          name: zenagent-desktop-${{ matrix.target }}
          path: |
            desktop/src-tauri/target/**/bundle/dmg/*.dmg
            desktop/src-tauri/target/**/bundle/macos/*.app
            desktop/src-tauri/target/**/bundle/nsis/*.exe
            desktop/src-tauri/target/**/bundle/appimage/*.AppImage
            desktop/src-tauri/target/**/bundle/deb/*.deb
          if-no-files-found: error
```

- 干跑（`workflow_dispatch`）没有 tag：tauri-action 的 `tagName`/`releaseName` 传空字符串，产物只经 `upload-artifact` 落在运行页；tag 推送时 `upload-artifact` 那步跳过，直接进 Release（避开 `publish.yml` 踩过的 artifact 配额坑）。

- [ ] **Step 3: 加 `latest.json` 聚合 job（矩阵之后）**

新增 `latest` job：`needs: build`、`runs-on: ubuntu-latest`、`if: startsWith(github.ref, 'refs/tags/v')`。
**注意 `secrets` 上下文在 job 级 `if` 里不可用**，所以判断 secret 是否存在必须放进 step：
第一个 step 用 `env: KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}` + `if: env.KEY != ''`
执行聚合；第二个 step 用 `if: env.KEY == ''` 在 release body 追加一句"本版未启用自更新"。
聚合步骤用 `gh release view --json assets` 列出 `.sig` 资产，按资产名映射到 `platform` 键
（`darwin-aarch64`、`darwin-x86_64`、`linux-x86_64`、`linux-aarch64`、`windows-x86_64`、
`windows-aarch64`），拼出完整 `latest.json`（`{"version": <tag 去掉 v>, "pub_date": ...,
"platforms": {...}}`），再 `gh release upload --clobber` 覆盖。

- [ ] **Step 4: 干跑验证**

Run: `gh workflow run desktop-release -R encyc/zen-agent` 然后 `gh run watch`
Expected: 六个 leg 全绿，`gh run download` 能拿到 6 份安装包（macOS 2 个 dmg、Windows 2 个 exe、Linux 2 AppImage + 2 deb）

- [ ] **Step 5: 文档**

`desktop/README.md` 新增 "Install" 段：下载哪个文件、macOS 未签名需要右键→打开或 `xattr -dr com.apple.quarantine`、Windows SmartScreen 提示、更新策略（设置里可关）、本地打完整包要先跑 `scripts/build-sidecar.py` 再带 `--config src-tauri/tauri.release.conf.json`。`CHANGELOG.md` Unreleased 记一条。

- [ ] **Step 6: Commit**

```bash
git add desktop/src-tauri/tauri.release.conf.json .github/workflows/desktop-release.yml \
        desktop/README.md CHANGELOG.md
git commit -m "ci(desktop): publish signed-off installers for six platform targets"
```
