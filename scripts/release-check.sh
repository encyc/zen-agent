#!/usr/bin/env bash
# Release gate: prove the artifacts are publishable before they reach PyPI.
#
#   bash scripts/release-check.sh                # build, then verify both installs
#   bash scripts/release-check.sh --skip-build   # verify the dist/ that was just built
#   bash scripts/release-check.sh --python 3.13  # override the test interpreter
#
# The test venvs default to the project's minimum supported Python (the
# `requires-python` floor), because that is where packaging bugs surface.
#
# Every check is a release blocker, nothing is uploaded, and nothing outside a
# temp dir is written, so it is safe to run on a clean tree before tagging:
#
#   bash scripts/check.sh && bash scripts/release-check.sh && git tag vX.Y.Z
#
# It covers what actually broke in past releases: `zen --version` missing, a dead
# module shipped inside the wheel, a stray tracked file shipped in the sdist, and
# an sdist that could not be installed by anyone but the author.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

SKIP_BUILD=0
PYTHON=""
while (($#)); do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --python)
      PYTHON="${2:-}"
      [[ -n "$PYTHON" ]] || { echo "release-check: --python needs a value" >&2; exit 2; }
      shift 2
      ;;
    -h | --help)
      awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
      exit 0
      ;;
    *)
      echo "release-check: unknown argument: $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

command -v uv >/dev/null || { echo "release-check: uv is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "release-check: python3 is required" >&2; exit 2; }

ok() { printf '  ok    %s\n' "$*"; }
note() { printf '  ..    %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() {
  printf '\nFAIL  %s\n' "$*" >&2
  exit 1
}

# ---------------------------------------------------------------- 1. version
step "version"

VERSION="$(sed -n 's/^version = "\([^"]*\)"$/\1/p' pyproject.toml | head -n 1)"
MODULE_VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' zenagent/__init__.py | head -n 1)"
[[ -n "$VERSION" ]] || die "could not read version from pyproject.toml"
[[ "$VERSION" == "$MODULE_VERSION" ]] ||
  die "pyproject.toml says $VERSION but zenagent/__init__.py says ${MODULE_VERSION:-<none>}"
ok "pyproject.toml = zenagent/__init__.py = $VERSION"

TAG_STATE="untracked"
if git rev-parse --git-dir >/dev/null 2>&1; then
  TAG_STATE="missing"
  for tag in $(git tag --points-at HEAD | grep -E '^v[0-9]' || true); do
    [[ "$tag" == "v$VERSION" ]] ||
      die "HEAD is tagged $tag but the version is $VERSION (bump the version or move the tag)"
  done
  if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null 2>&1; then
    if git tag --points-at HEAD | grep -qx "v$VERSION"; then
      TAG_STATE="head"
      ok "tag v$VERSION points at HEAD"
    else
      TAG_STATE="elsewhere"
      note "tag v$VERSION does not point at HEAD (already tagged: bump the version)"
    fi
  else
    note "HEAD is not tagged v$VERSION yet (expected before a release)"
  fi
fi

# ------------------------------------------------------------------ 2. build
step "build"

if ((SKIP_BUILD)); then
  [[ -d dist ]] || die "--skip-build needs an existing dist/ directory"
  note "reusing dist/ as built"
else
  uv build --clear
fi

shopt -s nullglob
WHEELS=(dist/*.whl)
SDISTS=(dist/*.tar.gz)
shopt -u nullglob
((${#WHEELS[@]} == 1)) || die "expected exactly one wheel in dist/, found ${#WHEELS[@]}"
((${#SDISTS[@]} == 1)) || die "expected exactly one sdist in dist/, found ${#SDISTS[@]}"
WHEEL="dist/$(basename "${WHEELS[0]}")"
SDIST="dist/$(basename "${SDISTS[0]}")"
[[ "$(basename "$WHEEL")" == "zenagent_py-$VERSION-"* ]] ||
  die "wheel does not carry version $VERSION: $WHEEL"
[[ "$(basename "$SDIST")" == "zenagent_py-$VERSION.tar.gz" ]] ||
  die "sdist does not carry version $VERSION: $SDIST"
ok "$(basename "$WHEEL")"
ok "$(basename "$SDIST")"
# `uv build` builds the wheel from the sdist, so a file missing from the sdist
# fails here instead of after publication.

# ------------------------------------------------------- 3. artifact contents
step "artifact contents"

python3 - "$WHEEL" "$SDIST" "$VERSION" <<'PY' || die "artifact contents are not publishable"
import sys
import tarfile
import zipfile

wheel_path, sdist_path, version = sys.argv[1:4]
dist_info = f"zenagent_py-{version}.dist-info"
junk_suffixes = (".append", ".orig", ".rej", ".bak", ".swp", ".tmp", ".pyc", ".DS_Store")
dev_dirs = ("tests/", "docs/", "evals/", "scripts/", ".github/", "examples/")
problems = []

with zipfile.ZipFile(wheel_path) as zf:
    entries = zf.infolist()
    wheel_names = [entry.filename for entry in entries]
    required = {
        "zenagent/__init__.py",
        "zenagent/cli/main.py",
        "zenagent/cli/tui/app.py",
        "zenagent/app/runtime.py",
        "zenagent/agent/agent.py",
        "zenagent/tools/web/providers.py",
        "zenagent/settings.py",
        f"{dist_info}/METADATA",
        f"{dist_info}/entry_points.txt",
    }
    missing = sorted(required - set(wheel_names))
    if missing:
        problems.append(f"wheel is missing {missing}")
    shipped_dev = [
        name
        for name in wheel_names
        if name.startswith(dev_dirs) or "__pycache__" in name or name.endswith(junk_suffixes)
    ]
    if shipped_dev:
        problems.append(f"wheel ships dev or junk files: {shipped_dev[:5]}")
    empty = [
        entry.filename
        for entry in entries
        if not entry.is_dir() and entry.file_size == 0
    ]
    if empty:
        problems.append(f"wheel ships empty files: {empty[:5]}")
    metadata = zf.read(f"{dist_info}/METADATA").decode("utf-8", "replace")
    for needle in (
        f"Version: {version}",
        "Name: zenagent-py",
        "Requires-Python: >=3.11",
        "Provides-Extra: cli",
        "Requires-Dist: textual",
    ):
        if needle not in metadata:
            problems.append(f"METADATA is missing {needle!r}")
    entry_points = zf.read(f"{dist_info}/entry_points.txt").decode("utf-8", "replace")
    if "zen = zenagent.cli.main:main" not in entry_points:
        problems.append("entry_points.txt does not define the zen command")

with tarfile.open(sdist_path) as tf:
    root = f"zenagent_py-{version}"
    members = [member.name for member in tf.getmembers()]
sdist_names = [name[len(root) + 1 :] if name.startswith(root + "/") else name for name in members]
required = {"pyproject.toml", "README.md", "zenagent/__init__.py", "zenagent/cli/tui/app.py"}
missing = sorted(required - set(sdist_names))
if missing:
    problems.append(f"sdist is missing {missing}")
# The build backend rewrites pyproject.toml and keeps the original beside it;
# that one is expected, unlike an editing leftover (`*.orig`, `*.append`, ...).
shipped_dev = [
    name
    for name in sdist_names
    if (name.startswith(dev_dirs) or "__pycache__" in name or name.endswith(junk_suffixes))
    and name != "pyproject.toml.orig"
]
if shipped_dev:
    problems.append(f"sdist ships dev or junk files: {shipped_dev[:5]}")

if problems:
    for problem in problems:
        print(f"  problem: {problem}", file=sys.stderr)
    sys.exit(1)
wheel_files = [name for name in wheel_names if not name.endswith("/")]
print(f"  ok    wheel: {len(wheel_files)} files, no dev/junk/empty files")
print(f"  ok    sdist: {len(sdist_names)} entries, pyproject.toml + README.md + the package only")
PY

# ------------------------------------------------------ 3b. prompt blocks
step "prompt blocks"

expected_blocks="$(find zenagent/prompts/blocks -name '*.md' -type f | wc -l | tr -d ' ')"
python3 - "$WHEEL" "$expected_blocks" <<'PY' || die "prompt blocks are not packaged"
import sys
import zipfile

wheel_path, expected = sys.argv[1], int(sys.argv[2])
problems = []
with zipfile.ZipFile(wheel_path) as zf:
    names = zf.namelist()
    blocks = [
        name
        for name in names
        if name.startswith("zenagent/prompts/blocks/") and not name.endswith("/")
    ]
    if len(blocks) != expected:
        problems.append(
            f"wheel ships {len(blocks)} prompt block files, expected {expected}"
        )
    if "zenagent/prompts/CHANGELOG.md" not in names:
        problems.append("wheel is missing zenagent/prompts/CHANGELOG.md")
if problems:
    for problem in problems:
        print(f"  problem: {problem}", file=sys.stderr)
    sys.exit(1)
print(f"  ok    prompt blocks: {len(blocks)} files")
PY

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# The venvs test the requires-python floor unless the caller pinned something.
TEST_PYTHON="$PYTHON"
if [[ -z "$TEST_PYTHON" ]]; then
  TEST_PYTHON="$(sed -nE 's/^requires-python = "[^0-9]*([0-9][0-9.]*).*"/\1/p' pyproject.toml | head -n 1)"
fi
[[ -n "$TEST_PYTHON" ]] || die "could not read the requires-python floor from pyproject.toml"

# ------------------------------------------------------- 4. kernel install
# `pip install zenagent-py` (no extras) must be a working library and CLI.
step "kernel install (no extras)"

uv venv -q --python "$TEST_PYTHON" "$TMP/kernel"
KPY="$TMP/kernel/bin/python"
[[ -x "$KPY" ]] || die "venv has no bin/python: $TMP/kernel"
ok "interpreter: $("$KPY" -c 'import platform; print(platform.python_version())')"
uv pip install -q --python "$KPY" "$ROOT/$WHEEL"
ZEN="$TMP/kernel/bin/zen"
[[ -x "$ZEN" ]] || die "the wheel did not install a zen console script"
ok "wheel installs without extras"

VERSION_OUT="$("$ZEN" --version)"
[[ "$VERSION_OUT" == "zenagent $VERSION" ]] ||
  die "zen --version printed ${VERSION_OUT:-<nothing>}, expected 'zenagent $VERSION'"
ok "zen --version: $VERSION_OUT"

"$KPY" - <<'PY' || die "the kernel does not import from an installed wheel"
import zenagent
import zenagent.agent.agent  # noqa: F401
import zenagent.cli.main  # noqa: F401
import zenagent.settings  # noqa: F401
import zenagent.tools.web.providers  # noqa: F401

print(f"  ok    kernel imports (zenagent {zenagent.__version__})")
PY

EXTRA_ERROR="$("$KPY" -c 'import zenagent.cli.tui' 2>&1 || true)"
if [[ "$EXTRA_ERROR" != *"No module named 'textual'"* ]]; then
  die "the TUI imported without the [cli] extra (textual leaked into the kernel): $EXTRA_ERROR"
fi
ok "no extras needed by the kernel (textual stays behind [cli])"

# A bare `zen` on a kernel-only install must explain the missing extras, not
# traceback (stdout is not a TTY here, so this takes the REPL path).
REPL_CODE=0
REPL_OUT="$(printf '' | "$ZEN" 2>&1)" || REPL_CODE=$?
[[ "$REPL_OUT" != *Traceback* ]] || die "a bare zen without extras crashed: $REPL_OUT"
[[ "$REPL_CODE" == 2 ]] || die "a bare zen without extras exited $REPL_CODE, expected 2"
[[ "$REPL_OUT" == *"needs the CLI extras"* ]] ||
  die "a bare zen without extras did not name the missing extras: $REPL_OUT"
ok "kernel-only zen points at the CLI extras instead of crashing"

# ------------------------------------------------------- 5. [cli] install
step "[cli] install"

uv venv -q --python "$TEST_PYTHON" "$TMP/full"
FPY="$TMP/full/bin/python"
[[ -x "$FPY" ]] || die "venv has no bin/python: $TMP/full"
uv pip install -q --python "$FPY" "zenagent-py[cli] @ file://$ROOT/$WHEEL"
FZEN="$TMP/full/bin/zen"
[[ -x "$FZEN" ]] || die "the [cli] install did not create a zen console script"
# Isolate the run: no developer settings, no developer session dir.
export HOME="$TMP/home"
mkdir -p "$HOME"
ok "wheel installs with [cli]"

"$FZEN" --help >/dev/null || die "zen --help failed"
PLAIN_VERSION="$("$FZEN" --version)"
[[ "$PLAIN_VERSION" == "zenagent $VERSION" ]] || die "zen --version said $PLAIN_VERSION"
ok "zen --version / --help"

"$FPY" - <<'PY' || die "the [cli] install cannot import its frontends"
from zenagent.cli.doctor import run_doctor  # noqa: F401
from zenagent.cli.repl import repl  # noqa: F401
from zenagent.cli.rpc import main as rpc_main  # noqa: F401
from zenagent.cli.tui import ZenAgentApp, run_tui  # noqa: F401

print(f"  ok    frontends import: tui app={ZenAgentApp.__name__}, repl, rpc, doctor")
PY

"$FPY" - <<'PY' || die "the search provider registry is incomplete in the wheel"
from zenagent.tools.web.providers import LABELS, PROVIDERS

expected = {"deepseek", "tavily", "brave", "bing", "duckduckgo"}
missing = expected - set(PROVIDERS)
if missing or set(LABELS) != set(PROVIDERS):
    raise SystemExit(f"providers={sorted(PROVIDERS)} labels={sorted(LABELS)} missing={sorted(missing)}")
print(f"  ok    search providers: {', '.join(sorted(PROVIDERS))}")
PY

"$FPY" - <<'PY' || die "the settings store does not persist a key from the installed wheel"
import stat
from pathlib import Path

from zenagent.settings import describe, get_api_key, set_api_key

set_api_key("DEEPSEEK_API_KEY", "sk-release-check")
if get_api_key("DEEPSEEK_API_KEY") != "sk-release-check":
    raise SystemExit("stored key did not read back")
path = Path.home() / ".zenagent" / "settings.json"
if not path.exists():
    raise SystemExit(f"{path} was not created")
mode = stat.S_IMODE(path.stat().st_mode)
if mode != 0o600:
    raise SystemExit(f"{path} has mode {mode:o}, expected 600")
if "sk-release-check" in str(describe()):
    raise SystemExit("describe() leaked the secret value")
print(f"  ok    settings round-trip ({path.name}, mode 600, no secret in describe())")
PY

DOCTOR_CODE=0
DOCTOR_OUT="$("$FZEN" doctor 2>&1)" || DOCTOR_CODE=$?
if ((DOCTOR_CODE > 1)) || [[ "$DOCTOR_OUT" == *Traceback* ]]; then
  printf '%s\n' "$DOCTOR_OUT" >&2
  die "zen doctor crashed (exit $DOCTOR_CODE)"
fi
ok "zen doctor runs (exit $DOCTOR_CODE without keys)"

# ----------------------------------------------------------------- summary
step "PASS  zenagent-py $VERSION is ready to publish"

digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}
for artifact in "$WHEEL" "$SDIST"; do
  printf '  %s  %s\n' "$(digest "$artifact")" "$(basename "$artifact")"
done
case "$TAG_STATE" in
  missing) printf '\nnext: git tag v%s && git push origin main v%s\n' "$VERSION" "$VERSION" ;;
  head) printf '\ntag v%s already points at HEAD: nothing left to tag.\n' "$VERSION" ;;
  elsewhere) printf '\nv%s is already tagged at another commit: bump the version before releasing again.\n' "$VERSION" ;;
  *) printf '\nnot a git checkout: tag and push the artifacts yourself.\n' ;;
esac
