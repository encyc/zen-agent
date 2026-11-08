# Hand-slip Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first safety layer for coding-agent mistakes: workspace path fence, protected credential paths, destructive-shell detection, and project-scoped write snapshots with `/restore`.

**Architecture:** All logic stays in the harness layer (`zenagent/app/`), the kernel is untouched. `app/safety.py` classifies risk (`Verdict`: allow / confirm / refuse) from a `GuardPolicy`; `app/permissions.py` turns verdicts into gate/broker decisions; `app/journal.py` captures pre-write bytes through the Agent's existing `before_tool_call`/`after_tool_call` hooks and restores by run; `app/restore.py` formats the `/restore` command shared by TUI and REPL.

**Tech Stack:** Python ≥3.11, stdlib only (hashlib/json/pathlib/uuid), pytest, existing `AgentTool`/`AgentEvent` contracts.

**Spec:** `docs/superpowers/specs/2026-11-01-hand-slip-guardrails-design.md` (read it before starting; it carries the rationale and the exact behavior matrix).

## Global Constraints

- Python ≥3.11 (`pyproject.toml`), no new third-party dependencies; kernel (`zenagent/agent/**`, `types`, `providers`) gets **zero** code changes.
- `zenagent/app/**` must not import `zenagent.cli/**`; new modules may import `zenagent.project`, `zenagent.config`, `zenagent.types`.
- All user-facing UI strings go through `zenagent.i18n.t()` with keys added to **both** `zenagent/i18n/en.py` and `zenagent/i18n/zh.py`; `tests/test_i18n.py::test_catalogs_are_key_and_placeholder_identical` must stay green. Model-facing text (gate refusal reasons, tool-result notices) stays plain English literals.
- Path handling: `Path(raw).expanduser()` then `.resolve()` — classification, journal, and approval preview must all use the same expansion so `~` never means two different files.
- Journal storage: directories mode `0o700`, files `0o600`; blobs are content-addressed by sha256 hex.
- Behavior changes are intentional and must be documented: full/`--yes` no longer auto-approve confirm-level risks; headless writes now produce backups unless `[backup] enabled = false`; `~` in fs tool paths is now expanded.
- Gate: `bash scripts/check.sh` (ruff + offline pytest) is the only definition of green.
- Commits: conventional style, one per task, on branch `feat/hand-slip-guardrails`.

## Review Focus

These are the failure modes the spec implies but whose tests live in exactly one task — a reviewer should check the owning task actually pins them:

1. **Symlink escape:** a workspace-internal symlink pointing outside must be classified `outside` (Task 1), and this must be the same resolved path the tool writes (Task 3).
2. **`~` consistency:** `write_file path="~/x"` must write `$HOME/x`, be gated against `$HOME/x`, and be journaled at `$HOME/x` (Tasks 1+3+4).
3. **Parallel same-file writes:** two tool calls writing one file in a run must restore the **pre-run** state, not an intermediate one (Task 4).
4. **Protected vs everyday:** `~/.zenagent/**` and `~/.ssh/**` are protected, but an in-project `.env` must NOT be (Task 1/2).
5. **Relative `rm`:** `rm -rf ./build` stays quiet; `rm -rf ../sibling` and `rm -rf .` trigger destructive (Task 1).
6. **Session rewrites:** `/undo` (new `*-rewrite.jsonl`), `/compact`, `/fork` change the session file but `/restore` must still see the previous run (Tasks 4+8+9).

---

### Task 1: Risk model and path classification (`app/safety.py`)

**Files:**
- Create: `zenagent/app/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Consumes: `zenagent.project.find_project_root`.
- Produces:
  - `PathScope = Literal["inside", "outside", "protected"]`, `RiskKind = Literal["outside", "protected", "destructive"]`
  - `@dataclass(frozen=True) class GuardPolicy: workspace: Path; allow_outside: tuple[Path, ...] = (); allow_protected: bool = False; allow_destructive: bool = False`
  - `@dataclass class Verdict: kind: Literal["allow","confirm","refuse"]; reason: str; risk: RiskKind | None = None; target: str | None = None`
  - `def workspace_root(cwd: Path | str | None = None) -> Path`
  - `def default_allow_outside() -> tuple[Path, ...]`
  - `def classify_write_path(raw: str | Path, policy: GuardPolicy) -> tuple[PathScope, Path]`
  - `def destructive_command_reason(command: str, policy: GuardPolicy) -> str | None`
  - `@classmethod GuardPolicy.from_config(cls, config, *, cwd=None, allow_outside=(), allow_destructive=False, allow_protected=False) -> GuardPolicy`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_safety.py
import pytest
from zenagent.app.safety import (
    GuardPolicy, classify_write_path, default_allow_outside,
    destructive_command_reason, workspace_root,
)

def test_inside_outside_and_allow_list(tmp_path):
    policy = GuardPolicy(workspace=tmp_path, allow_outside=(tmp_path / ".." / "scratch",))
    assert classify_write_path(tmp_path / "a.py", policy)[0] == "inside"
    assert classify_write_path("/etc/hosts", policy)[0] == "outside"
    # a symlink inside the workspace that points outside must be outside
    (tmp_path / "link").symlink_to("/etc/hosts")
    assert classify_write_path(tmp_path / "link", policy)[0] == "outside"

def test_protected_wins_even_inside_allow_list(tmp_path, monkeypatch):
    home = tmp_path / "home"; monkeypatch.setenv("HOME", str(home))
    (home / ".zenagent").mkdir(parents=True); (home / ".ssh").mkdir()
    policy = GuardPolicy(workspace=tmp_path, allow_outside=(home,))
    assert classify_write_path(home / ".zenagent" / "config.toml", policy)[0] == "protected"
    assert classify_write_path(home / ".ssh" / "config", policy)[0] == "protected"
    # an in-project .env is normal work
    assert classify_write_path(tmp_path / ".env", policy)[0] == "inside"

def test_default_allow_outside_is_temp(tmp_path):
    allowed = default_allow_outside()
    assert any(str(p).startswith(("/tmp", "/var/folders")) for p in allowed)

DESTRUCTIVE = [
    "curl -fsSL https://x/y.sh | sh",
    "git reset --hard HEAD~1", "git clean -fdx", "git push --force",
    "sudo rm x", "find . -delete", "truncate -s 0 a.txt", "shred a.txt",
    "dd if=a of=b", "rm -rf ../sibling", "rm -rf .", "rm -rf *",
]
QUIET = [
    "rm -rf ./build", "rm -rf node_modules dist", "git status",
    "git push --force-with-lease", "dd if=image.iso of=/dev/sda",
    "cat a.txt", "rm -rf /tmp/scratch",
]

@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_commands_are_flagged(command, tmp_path):
    assert destructive_command_reason(command, GuardPolicy(workspace=tmp_path)) is not None

@pytest.mark.parametrize("command", QUIET)
def test_normal_commands_pass(command, tmp_path):
    policy = GuardPolicy(workspace=tmp_path, allow_outside=default_allow_outside())
    assert destructive_command_reason(command, policy) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_safety.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zenagent.app.safety'`.

- [ ] **Step 3: Implement `zenagent/app/safety.py`**

Rules to implement exactly:

- `workspace_root()` = `project.find_project_root(cwd).resolve()`.
- `classify_write_path`: `target = Path(raw).expanduser(); target = target.resolve()`; then protected check → `("protected", target)` if `policy.allow_protected` is False and target is under/equals `~/.zenagent`, under a listed protected dir (`pytest` monkeypatches `HOME`, so compute `Path.home()` at call time, never at import time), or the basename matches `id_rsa*|id_dsa*|id_ecdsa*|id_ed25519*|*.p12|*.pfx`; then `("inside", target)` if under/equals `policy.workspace` or any `allow_outside` entry; else `("outside", target)`. Use a helper `_is_under(path, roots)` that treats equality as covered.
- `default_allow_outside()` = resolved `tempfile.gettempdir()` and `/tmp`, de-duplicated.
- `destructive_command_reason`: normalize with `" ".join(command.lower().split())`; return a short English reason for each group:
  - `(curl|wget)… | (sudo )?(sh|bash|zsh|python|perl|ruby)` — "download piped into an interpreter";
  - `git reset --hard`, `git clean -f`/`-fd`/`-fdx`, `git checkout -- .`, `git restore .`, `git stash drop|clear`, `git push --force` (but NOT `--force-with-lease`);
  - leading `sudo`;
  - `find … -delete`, `find … -exec rm`;
  - `truncate -s 0`, `shred`, `dd … of=` where the value does not start with `/dev/`.
  - recursive `rm` (any of `-r`/`-R`/`--recursive`): split the command on `[;&|\n]`, strip a leading `sudo`, take tokens not starting with `-` after `rm`; for each target with glob chars (`*?[`) return "recursive delete with a glob target"; otherwise resolve `Path(target).expanduser()` (relative against `Path.cwd()`) and return a reason when the resolved path equals `Path.cwd()`, equals/contains the workspace, lies outside `policy.allow_outside`, or is protected.
- `GuardPolicy.from_config`: build from `config.safety_allow_outside` (already absolute strings after Task 2), CLI `allow_outside` values, `config.safety_allow_destructive/protected` OR the CLI booleans, plus `default_allow_outside()`; resolve and de-duplicate.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_safety.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/safety.py tests/test_safety.py
git commit -m "feat(guard): workspace path classification and destructive command policy"
```

---

### Task 2: `[safety]` / `[backup]` config sections

**Files:**
- Modify: `zenagent/config.py` (`UserConfig`, `_load_config_file`, `_merge_user_configs`, `load_user_config`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces new `UserConfig` fields: `safety_allow_outside: list[str]`, `safety_allow_destructive: bool = False`, `safety_allow_protected: bool = False`, `backup_enabled: bool = True`, `backup_max_file_bytes: int = 2 * 1024 * 1024`, `backup_keep_runs: int = 50`.
- `_load_config_file(target, *, project_layer: bool = False)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_safety_and_backup_sections_parse(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[safety]\nallow_outside = ["~/scratch", "notes"]\nallow_destructive = true\n'
        '[backup]\nenabled = false\nmax_file_bytes = 4096\nkeep_runs = 7\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    loaded = load_user_config(cfg)
    assert loaded.safety_allow_outside == [str(tmp_path / "home" / "scratch"), str(tmp_path / "notes")]
    assert loaded.safety_allow_destructive is True
    assert loaded.backup_enabled is False
    assert loaded.backup_max_file_bytes == 4096
    assert loaded.backup_keep_runs == 7

def test_project_layer_may_not_set_safety(tmp_path, monkeypatch):
    project_cfg = tmp_path / "proj" / ".zenagent" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text("[safety]\nallow_destructive = true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="global-only"):
        load_user_config(cwd=tmp_path / "proj")

def test_unknown_keys_in_new_sections_are_errors(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[backup]\nnope = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys"):
        load_user_config(cfg)
```

Relative `allow_outside` entries resolve against the config file's directory; `~` expands against `HOME`; both are stored as absolute strings.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q -k "safety or backup"`
Expected: FAIL (fields missing / sections rejected).

- [ ] **Step 3: Implement parsing and merge**

- `_load_config_file`: after the `[tools]` block, parse `[safety]` (allowed keys `allow_outside`, `allow_destructive`, `allow_protected`) and `[backup]` (`enabled`, `max_file_bytes`, `keep_runs`); validate types/positivity, raise `ValueError(f"config {target}: [safety] has unknown keys …")` style errors. Expand/normalize `allow_outside` to absolute strings. When `project_layer=True` and either section is present, raise `ValueError(f"config {target}: [safety] and [backup] are global-only; move them to {config_path()}")`.
- `load_user_config`: pass `project_layer=True` for the project file call; `_merge_user_configs` copies the new fields from `base` (project layer cannot override them).
- Keep existing behavior untouched for missing files and explicit `path=` (explicit path is the global layer, not project).

- [ ] **Step 4: Run the full config tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/config.py tests/test_config.py
git commit -m "feat(config): [safety] and [backup] sections, global-only in the project layer"
```

---

### Task 3: Expand `~` in filesystem tool paths

**Files:**
- Modify: `zenagent/tools/fs.py` (`_read_file`, `_write_file`, `_edit_file`, `_list_dir`)
- Test: `tests/test_tools_fs.py`

**Interfaces:**
- Produces `def _arg_path(value: str) -> Path` returning `Path(value).expanduser()`; all four tools use it.

- [ ] **Step 1: Write the failing test**

```python
async def test_paths_expand_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = await WRITE_FILE.execute("call", {"path": "~/note.txt", "content": "hi"})
    assert not result.is_error
    assert (tmp_path / "note.txt").read_text() == "hi"
    read = await READ_FILE.execute("call", {"path": "~/note.txt"})
    assert read.content[0].text == "hi"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_tools_fs.py::test_paths_expand_home -v`
Expected: FAIL (file lands under `cwd/~/note.txt` or the read fails).

- [ ] **Step 3: Implement**

Add `_arg_path()` next to the other helpers and replace `path = Path(args["path"])` in the four tools (`read_file`, `write_file`, `edit_file`, `list_dir`) with `path = _arg_path(args["path"])`. Do not touch `edit_file`'s matching logic or the truncation guard.

- [ ] **Step 4: Run the fs tests**

Run: `uv run pytest tests/test_tools_fs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/tools/fs.py tests/test_tools_fs.py
git commit -m "fix(tools): expand ~ in filesystem tool paths"
```

---

### Task 4: Project-scoped snapshot journal (`app/journal.py`)

**Files:**
- Modify: `zenagent/project.py` (add `project_backups_dir`)
- Create: `zenagent/app/journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- `project.project_backups_dir(cwd=None) -> Path` = `project_dir(cwd) / "backups"`.
- `@dataclass(frozen=True) class BackupPolicy: enabled: bool = True; max_file_bytes: int = 2 * 1024 * 1024; keep_runs: int = 50`
- `def load_backup_policy(config) -> BackupPolicy`
- `def journal_for(session_path: Path | None, policy: BackupPolicy | None = None, *, cwd=None) -> SnapshotJournal | None`
- `@dataclass(frozen=True) class RunInfo: run: str; session: str; started: str; files: int; paths: tuple[str, ...]`
- `@dataclass class RestoreReport: run: RunInfo | None; restored: list[str]; skipped: list[tuple[str, str]]`
- `class SnapshotJournal:` `on_event(event)`, `async before_tool_call(tool_call, args) -> None`, `async after_tool_call(tool_call, result) -> ToolResultAgent | None`, `runs(limit=10) -> list[RunInfo]`, `restore(run_id: str) -> RestoreReport`.
- `def attach_journal(agent, journal) -> None` — composes the existing `agent.before_tool_call` with `journal.before_tool_call`, sets `agent.after_tool_call = journal.after_tool_call`, and `agent.subscribe(journal.on_event)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_journal.py
import json
from zenagent.app.journal import BackupPolicy, SnapshotJournal
from zenagent.types import ToolCall

async def _write(journal, path, content, call_id="c1"):
    call = ToolCall(id=call_id, name="write_file", arguments={"path": str(path)})
    await journal.before_tool_call(call, {"path": str(path), "content": content})
    return call

async def test_capture_commit_and_restore(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    journal.on_event(type("E", (), {"type": "agent_start"})())
    path = tmp_path / "a.txt"; path.write_text("old")
    call = await _write(journal, path, "new")
    path.write_text("new")
    result = await journal.after_tool_call(call, _ok("new"))
    assert result is None and len(journal.runs()) == 1
    path.write_text("user edit")
    report = journal.restore(journal.runs()[0].run)
    assert report.skipped and not report.restored        # hash mismatch is skipped
    path.write_text("new")
    report = journal.restore(journal.runs()[0].run)
    assert report.restored == [str(path)] and path.read_text() == "old"

async def test_created_file_is_deleted_on_restore(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    journal.on_event(type("E", (), {"type": "agent_start"})())
    path = tmp_path / "new.txt"
    call = await _write(journal, path, "x")
    path.write_text("x")
    await journal.after_tool_call(call, _ok("x"))
    journal.restore(journal.runs()[0].run)
    assert not path.exists()

async def test_too_large_is_skipped_and_reported(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy(max_file_bytes=4))
    journal.on_event(type("E", (), {"type": "agent_start"})())
    path = tmp_path / "big.bin"; path.write_bytes(b"x" * 10)
    call = await _write(journal, path, "new")
    path.write_bytes(b"new")
    result = await journal.after_tool_call(call, _ok("new"))
    assert result is not None and "backup skipped" in " ".join(b.text for b in result.content)

async def test_parallel_writes_restore_pre_run_state(tmp_path):
    # two calls captured before either executes -> one restore target = pre-run bytes
    ...
```

`_ok(text)` helper returns a minimal successful `ToolResultAgent` (same shape the loop produces). Also cover: `is_error` result drops the snapshot; `runs()` groups by run id; `keep_runs` prune rewrites manifest and deletes orphan blobs; manifest reader skips a corrupt line; dirs are `0o700`, files `0o600`; restore handles missing blob (skip "blob missing").

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_journal.py -q`
Expected: FAIL (`zenagent.app.journal` missing).

- [ ] **Step 3: Implement**

- `project_backups_dir`: mirrors `project_sessions_dir`.
- `journal_for`: return `None` when the policy is disabled; else `SnapshotJournal(project.project_backups_dir(cwd), policy)`.
- Journal internals: `manifest.jsonl` append (`json.dumps(..., ensure_ascii=False)` + `"\n"`, `os.O_APPEND`), `blobs/<sha[:2]>/<sha>` written via tmp + `os.replace`, `asyncio.Lock` around pending/manifest. `on_event`: `agent_start` → new `run = uuid4().hex[:12]`, `turn = 0`; `turn_start` → `turn += 1`. `before_tool_call` captures only `write_file`/`edit_file` (key = `getattr(tool_call, "id", None) or str(tool_call)`), reading bytes or `None`; over `max_file_bytes` → `skipped="too_large"` without reading; never raises. `after_tool_call`: discard on `result.is_error`; else read current bytes, compute `after_sha256`, write blob for `modified`, append manifest, return `None`; when skipped, return `dataclasses.replace(result, content=[*result.content, TextContent(text=notice)])`. `runs`/`restore` read the manifest, tolerate bad lines, group by run preserving `seq`, and implement the spec's earliest-blob + last-hash verification; `restore` skips with reasons `"changed since the agent wrote it"`, `"blob missing"`, `"backup skipped (too large)"`, `"missing"`. `__init__` prunes to `keep_runs`.
- `attach_journal`: await-aware composition (`inspect.isawaitable`), block result wins, journal never blocks; subscribe `on_event`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_journal.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/project.py zenagent/app/journal.py tests/test_journal.py
git commit -m "feat(journal): project-scoped write snapshots with restore"
```

---

### Task 5: Risk verdicts in the permission gate

**Files:**
- Modify: `zenagent/app/permissions.py`
- Test: `tests/test_permissions.py`

**Interfaces:**
- Consumes: Task 1 (`GuardPolicy`, `Verdict`, `classify_write_path`, `destructive_command_reason`, `workspace_root`, `default_allow_outside`), Task 2 fields.
- Produces:
  - `def build_permission_gate(*, interactive: bool, assume_yes: bool = False, allow_dangerous: bool = False, allow: list[str] | None = None, deny: list[str] | None = None, mode: str = DEFAULT_MODE, read_only: bool = False, policy: GuardPolicy | None = None) -> PermissionGate`
  - `PermissionGate.__init__(..., policy: GuardPolicy | None = None)`; `PermissionGate.assess(tool_name, args, tool=None) -> Verdict`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_outside_write_is_confirm_not_refuse(tmp_path):
    gate = build_permission_gate(interactive=False, policy=GuardPolicy(workspace=tmp_path))
    allowed, reason = await gate.check("write_file", {"path": "/etc/hosts", "content": "x"})
    assert allowed is False and "--allow-outside" in reason

async def test_full_mode_does_not_bypass_outside(tmp_path):
    gate = build_permission_gate(interactive=False, assume_yes=True, mode="full",
                                 policy=GuardPolicy(workspace=tmp_path))
    assert (await gate.check("write_file", {"path": "/etc/hosts", "content": "x"}))[0] is False

async def test_tools_allow_cannot_bypass_risk(tmp_path):
    gate = build_permission_gate(interactive=False, allow=["*"], assume_yes=True,
                                 policy=GuardPolicy(workspace=tmp_path))
    assert (await gate.check("write_file", {"path": "/etc/hosts", "content": "x"}))[0] is False

async def test_allow_outside_flag_resolves_the_risk(tmp_path):
    other = tmp_path / "other"; other.mkdir()
    gate = build_permission_gate(interactive=False, assume_yes=True,
                                 policy=GuardPolicy(workspace=tmp_path / "workspace",
                                                    allow_outside=(other,)))
    assert (await gate.check("write_file", {"path": other / "x", "content": "x"}))[0] is True

async def test_interactive_risk_answer_always_is_target_scoped(tmp_path, monkeypatch):
    ...  # monkeypatch input() -> "a"; assert second identical call is allowed without input()
```

Also: plan/read-only still refuse outside writes **and** destructive bash; catastrophic bash still refused under `--yes`; read tools unaffected; `~/x` and `$HOME/x` classify identically.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_permissions.py -q -k "risk or outside or destructive or bypass"`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `assess()` implements the exact order from the spec (`refuse` for catastrophic/deny/plan/read-only; `confirm` for destructive/protected/outside; else `allow`), using `tool_target()`-style resolution. Add `_risk_hint(risk)` returning the flag hint strings (`--allow-outside PATH`, `--allow-destructive`, `--allow-protected`).
- `check()`: `refuse → (False, reason)`; `confirm` → allowed if the exact target is in `self._risk_grants`, else interactive `_ask` with risk label, else `(False, reason + hint)`; `allow` → the pre-existing flow (allow patterns, read-only, target grants, full/`--yes`, interactive ask, deny).
- `_ask()` prefixes `t("perm.risk_outside"| "perm.risk_protected" | "perm.risk_destructive", …)` when a risk is present; answer `"a"` records the target in `_risk_grants` (risk) or the existing `_always_allowed` (normal). Add the i18n keys to both catalogs now.
- `policy_refusal()` keeps its signature and returns a reason only for `refuse` verdicts.
- `build_permission_gate` fills the default policy (`workspace_root()`, `default_allow_outside()`).

- [ ] **Step 4: Run the permission tests**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: PASS (update any old expectation that assumed full mode auto-allows outside writes — there is none today, so this should be additive).

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/permissions.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_permissions.py
git commit -m "feat(permissions): risk verdicts and workspace fence in the gate"
```

---

### Task 6: Broker asks for risky calls even in full mode

**Files:**
- Modify: `zenagent/app/permissions.py` (`PermissionBroker`)
- Test: `tests/test_app_permissions.py`

**Interfaces:**
- `PermissionBroker.set_confirm(callback)` where `callback(tool_name: str, arguments: dict, verdict: Verdict) -> Awaitable[str]`; answers stay `allow|always|deny`.
- Broker keeps `assess()` in the loop and a `_risk_targets: set[str]` for risk grants.

- [ ] **Step 1: Write the failing tests**

```python
async def test_full_mode_still_asks_for_outside_write(tmp_path):
    calls = []
    async def confirm(name, args, verdict):
        calls.append((name, verdict.risk)); return "allow"
    gate = build_permission_gate(interactive=False, mode="full", policy=GuardPolicy(workspace=tmp_path))
    broker = PermissionBroker.wrap(gate); broker.set_confirm(confirm)
    assert await broker.before_tool_call("write_file", {"path": "/etc/hosts", "content": "x"}) is None
    assert calls == [("write_file", "outside")]

async def test_ordinary_write_in_full_mode_does_not_ask(...):
    ...  # path inside tmp_path -> no confirm call
async def test_always_on_risk_is_target_scoped(...):
    ...  # answer "always" twice: one prompt; a different outside path prompts again
async def test_plan_mode_still_refuses(...):
    ...  # reason contains "plan mode"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app_permissions.py -q`
Expected: FAIL (callback arity / full-mode behavior).

- [ ] **Step 3: Implement**

Broker flow: `verdict = self._gate.assess(...)`; `refuse → block`; `confirm →` allow if target in `_risk_targets` or no confirm callback available? (no callback means block with reason); else modal with verdict, `"always"` adds target to `_risk_targets`; `allow →` existing logic (target grants, read-only, allow patterns, `mode == "full"`, confirm callback, block). Never let `full` or `--yes` shortcut a `confirm` verdict. Update all existing broker tests/callers to the 3-argument callback contract.

- [ ] **Step 4: Run the app-permission tests**

Run: `uv run pytest tests/test_app_permissions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/permissions.py tests/test_app_permissions.py
git commit -m "feat(permissions): broker confirms risky calls even in full mode"
```

---

### Task 7: Risk badge and diff preview in the TUI approval dialog

**Files:**
- Modify: `zenagent/cli/tui/app.py` (`request_tool_approval`), `zenagent/cli/tui/screens/modals.py` (`ToolApproval`, new `preview_diff`)
- Test: `tests/test_tui_approval.py`

**Interfaces:**
- `ZenAgentApp.request_tool_approval(self, tool_name, arguments, verdict=None) -> str` (passes `verdict` from the broker).
- `def preview_diff(tool_name: str, arguments: dict, *, max_lines: int = 12) -> list[str]` in `modals.py`: for `write_file` read the current file text and diff against `content`; for `edit_file` apply `old_string`→`new_string` when it matches, else `[]`; use `zenagent.tools.fs.unified_diff`; prefix `"…"` when truncated; return `[]` for binary/missing files or other tools.
- `ToolApproval(..., verdict: Verdict | None = None)` renders a risk line using `t("tui.dialog.risk_outside"/"risk_protected"/"risk_destructive", …)` above the body, plus a bounded diff block.

- [ ] **Step 1: Write the failing tests**

```python
def test_approval_shows_outside_risk_badge(picker_app):
    screen = ToolApproval("write_file", {"path": "/etc/hosts", "content": "x"},
                          verdict=Verdict("confirm", "outside workspace", risk="outside", target="/etc/hosts"))
    # mount and assert the rendered text contains the i18n risk label and target

def test_approval_shows_diff_preview(tmp_path):
    target = tmp_path / "a.txt"; target.write_text("one\ntwo\n")
    lines = preview_diff("edit_file", {"path": str(target), "old_string": "two", "new_string": "three"})
    assert any(line.startswith("+") for line in lines) and any(line.startswith("-") for line in lines)
```

Also: `preview_diff` truncates at `max_lines`; missing file returns `[]`; `write_file` preview works; no preview for `bash`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tui_approval.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app.py` passes `verdict=...` into `ToolApproval`; `modals.py` renders the badge with a `dialog-note`/`dialog-warning` class (reuse existing CSS classes where possible; add one class if needed in the TUI stylesheet) and the diff lines; add the three i18n keys to both catalogs.

- [ ] **Step 4: Run the TUI approval tests**

Run: `uv run pytest tests/test_tui_approval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/tui/app.py zenagent/cli/tui/screens/modals.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_tui_approval.py
git commit -m "feat(tui): risk badges and diff preview in the approval dialog"
```

---

### Task 8: `/restore` core and TUI command

**Files:**
- Create: `zenagent/app/restore.py`
- Modify: `zenagent/app/commands.py` (register `restore`), `zenagent/app/runtime.py` (expose `runtime.journal`)
- Test: `tests/test_restore.py`

**Interfaces:**
- `def run_restore_command(journal: SnapshotJournal | None, argument: str, *, running: bool) -> str` — `""` restores the latest run, `"list"` lists up to 10 runs, `"<n>"` restores the n-th listed run; returns the i18n-formatted message; handles unavailable journal, running run, bad argument.
- `AppServices.runtime.journal` is the live `SnapshotJournal | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_restore_latest_and_list(tmp_path, capsys):
    journal = SnapshotJournal(tmp_path / "b", BackupPolicy())
    ...  # seed two runs through the journal API
    assert "restored" in run_restore_command(journal, "", running=False)
    listed = run_restore_command(journal, "list", running=False)
    assert "1." in listed
    assert "cannot restore" in run_restore_command(journal, "", running=True)
    assert "unavailable" in run_restore_command(None, "", running=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_restore.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`run_restore_command` validates the argument, calls `journal.restore(...)`/`journal.runs(limit=10)`, formats with new `cmdmsg.restore_*` i18n keys (paths counted, skipped reasons listed, run/session shown). Register `@_command("restore")` in `app/commands.py` with `services.runtime.journal` and `services.chat.status.running`. Add `self.journal: SnapshotJournal | None = None` to `Runtime.__init__`.

- [ ] **Step 4: Run the restore tests**

Run: `uv run pytest tests/test_restore.py tests/test_app_commands.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/restore.py zenagent/app/commands.py zenagent/app/runtime.py zenagent/i18n/en.py zenagent/i18n/zh.py tests/test_restore.py
git commit -m "feat(app): /restore command and restore core"
```

---

### Task 9: Wire guard policy and journal into every frontend

**Files:**
- Modify: `zenagent/cli/main.py` (flags, TUI broker construction, headless), `zenagent/cli/repl.py` (gate, journal, `/restore` branch), `zenagent/cli/rpc/` (gate helper), `zenagent/app/runtime.py` (`RuntimeOptions` fields, journal creation + attach in `bootstrap`)
- Test: `tests/test_cli.py`, `tests/test_repl_logic.py`

**Interfaces:**
- `RuntimeOptions` new fields: `allow_outside: list[str] = field(default_factory=list)`, `allow_destructive: bool = False`, `allow_protected: bool = False`.
- Flags: `--allow-outside PATH` (`action="append"`), `--allow-destructive`, `--allow-protected`.
- `repl(...)` gains `allow_outside`, `allow_destructive`, `allow_protected`, and `journal: SnapshotJournal | None` is created after the session; `handle_slash` gains an `restore=None` callback used by the new `"restore"` branch.
- RPC builds its gate through `build_permission_gate` with the default policy (no journal).

- [ ] **Step 1: Write the failing tests**

```python
def test_parser_accepts_guard_flags():
    args = cli_main.build_parser().parse_args(
        ["--allow-outside", "/tmp/a", "--allow-outside", "/tmp/b",
         "--allow-destructive", "--allow-protected"])
    assert args.allow_outside == ["/tmp/a", "/tmp/b"]
    assert args.allow_destructive and args.allow_protected

async def test_headless_creates_backup_for_write(monkeypatch, tmp_path):
    # use the existing _build_agent seam but let the real Agent run one fake tool call,
    # then assert project_backups_dir()/manifest.jsonl exists and a run is restorable
```

Also: `Runtime.bootstrap` attaches a journal to the agent (assert `runtime.journal is not None` and `agent.before_tool_call` is not the raw gate hook when backups are enabled); `[backup] enabled = false` yields `runtime.journal is None`; REPL `handle_slash("restore", …)` prints the restore core output.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py tests/test_repl_logic.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

- Build the policy once per frontend: TUI broker and headless/REPL/RPC gate use `GuardPolicy.from_config(user_config, cwd=Path.cwd(), allow_outside=flags, allow_destructive=…, allow_protected=…)` passed to `build_permission_gate`.
- `Runtime.bootstrap`: after the session is open and the gate is resolved, create `runtime.journal = journal_for(runtime.session.path, load_backup_policy(config))`; after the Agent is built, `attach_journal(agent, journal)` and keep the existing subscribe order.
- REPL: same journal creation + attach after the Agent; add the `restore` branch to `handle_slash` (calls `run_restore_command`, `running=agent.is_streaming`) and wire the callback from `repl`.
- Headless: after `_build_agent` returns, create the journal and `attach_journal`; no `/restore` surface (documented).
- RPC: replace the ad-hoc `PermissionGate(...)` with `build_permission_gate(...)`; no journal.
- Update the TUI broker construction in `main.py` to pass the policy.

- [ ] **Step 4: Run the frontend tests**

Run: `uv run pytest tests/test_cli.py tests/test_repl_logic.py tests/test_rpc.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/main.py zenagent/cli/repl.py zenagent/cli/rpc/ zenagent/app/runtime.py tests/test_cli.py tests/test_repl_logic.py
git commit -m "feat(cli): wire guard policy and write journal into all frontends"
```

---

### Task 10: Docs, changelog, and end-to-end acceptance

**Files:**
- Modify: `docs/guide/tools-permissions.md`, `docs/guide/config.md`, `docs/internals/guardrails.md`, `docs/feature-map.md`, `README.md`, `CHANGELOG.md`
- Test: `bash scripts/check.sh`

- [ ] **Step 1: Update the docs**

- `tools-permissions.md`: new sections for workspace fence, protected paths, destructive tier + the `--allow-*` flags, `/restore` usage, and the corrected dangerous-command list (now including the real `curl | sh` detection); state the best-effort boundary for bash.
- `config.md`: `[safety]` and `[backup]` tables with defaults and the global-only rule; note headless backup behavior.
- `guardrails.md`: replace the "不做" list items that are now done (workspace fence, backups) and keep the honest remaining gaps (no OS sandbox, no read gating).
- `feature-map.md`: one row for the guard layer (safety/journal/restore → `app/safety.py`, `app/journal.py`, `app/restore.py` → tests).
- `README.md`: flags in the CLI table, `/restore` in the command table.
- `CHANGELOG.md`: add `## Unreleased` with the behavior changes (full-mode confirmations, headless backups, `~` expansion) and the new features.

- [ ] **Step 2: Run the full gate**

Run: `bash scripts/check.sh`
Expected: ruff clean, all tests pass.

- [ ] **Step 3: Manual acceptance (documented in the commit message)**

```bash
uv run zen -p "write hello to /tmp/zen-accept.txt" --yes     # allowed (temp)
uv run zen -p "write hello to ~/zen-accept.txt" --yes        # refused with --allow-outside hint
# TUI: full mode, ask it to edit /etc/hosts -> outside badge; /restore restores the last write
```

Expected: the four behaviors match the spec's 验收标准 (items 1–7).

- [ ] **Step 4: Commit**

```bash
git add docs README.md CHANGELOG.md
git commit -m "docs: hand-slip guardrails guide, config, changelog"
```

---

## Plan self-review

- **Spec coverage:** fence (Tasks 1/5/9), protected paths (1/2), destructive bash incl. curl fix (1/5), snapshots + restore semantics + retention + permissions (4/8), frontends incl. REPL-registry reality and RPC exemption (9), config/flags/global-only (2/9), UX badges + diff (6/7), docs + changelog (10). Design's "fix `~`" bug → Task 3.
- **Type consistency:** `GuardPolicy`/`Verdict` defined once in Task 1 and reused; `SnapshotJournal` API in Task 4 consumed by 8/9; `build_permission_gate` defined in Task 5 used in 6/9; `run_restore_command` defined in Task 8 used by 9.
- **Review Focus tests:** symlink (1), `~` (1+3+4), parallel writes (4), protected vs `.env` (1), relative rm (1), session rewrites (4/8/9 integration).
- **Proportion:** signatures, test names, and exact values only; bodies are left to the implementer except where the spec pins the behavior.

## Execution handoff

Tasks are interface-coupled (1→5→6→9; 2→4→8→9), so a fresh subagent per task with a reviewer between tasks is the safer default; native execution in one session is faster but relies on the plan's interfaces staying as written.
