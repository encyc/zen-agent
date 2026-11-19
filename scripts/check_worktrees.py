#!/usr/bin/env python3
"""The worktree hygiene gate: merged worktrees are litter, retired at once.

Parallel feature branches leave worktrees behind (`git worktree` per feature,
plus tool-managed ones under `.agents/worktrees` and outside the repo). Once a
branch is merged and its checkout is clean, the worktree no longer earns its
disk, its `git status` noise or its place in the worktree list — but unlike a
stale branch it is invisible in review, so it accumulates silently. This gate
makes the accumulation fail loudly instead.

Rules (checked per registered worktree, main checkout exempt):

* **RETIRE** — the checked-out commit is fully merged into the primary branch
  AND the checkout is clean. `--apply` removes it (`git worktree remove`) and
  deletes its branch with `-d`, which itself refuses anything unmerged.
* **KEEP-ACTIVE** — the branch carries commits not in the primary branch.
  Work in progress; never touched.
* **KEEP-DIRTY** — merged, but the checkout has uncommitted changes. Only a
  human can decide whether those edits matter.
* **STRAY** — a detached HEAD that is not an ancestor of the primary branch.
  Nobody owns it in review; flagged for manual inspection.
* **KEEP-LOCKED** — `git worktree lock`ed; the lock is the owner's decision.
* A worktree whose directory vanished is judged by its branch alone and is
  swept by `git worktree prune` under `--apply`.

Merged branches that no longer sit in any worktree are only *reported* —
deleting them is a naming decision, not a hygiene one.

Modes: default is gate mode (exit 1 when anything is retirable, so check.sh
holds the line locally and CI stays clean at exactly one worktree); ``--report``
lists everything and always exits 0; ``--apply`` performs the retirement.
Stdlib only, like check_architecture.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _git(*args: str, cwd: Path = REPO) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return out.stdout


def _primary_branch() -> str:
    """The branch this repo treats as trunk: origin/HEAD, falling back to main."""
    try:
        ref = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
        if ref.startswith("origin/"):
            return ref[len("origin/"):]
    except subprocess.CalledProcessError:
        pass
    return "main"


def _worktrees() -> list[dict]:
    """Parse `git worktree list --porcelain`; the first entry is the main one."""
    entries: list[dict] = []
    current: dict = {}
    for line in _git("worktree", "list", "--porcelain").splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = {"path": Path(value), "detached": False, "locked": False,
                       "prunable": False}
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "HEAD":
            current["head"] = value
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = True
        elif key == "prunable":
            current["prunable"] = True
    if current:
        entries.append(current)
    return entries


def _is_merged(commit: str, primary: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, primary],
        cwd=REPO, capture_output=True, text=True,
    ).returncode == 0


def _dirty(path: Path) -> bool:
    if not path.is_dir():
        return False
    return bool(_git("status", "--porcelain", cwd=path).strip())


def _head(path: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=path).strip()


def classify(primary: str) -> list[tuple[str, dict]]:
    """Attach a verdict to every worktree, in `git worktree list` order."""
    out: list[tuple[str, dict]] = []
    for i, wt in enumerate(_worktrees()):
        if i == 0:
            out.append(("MAIN", wt))
        elif wt["locked"]:
            out.append(("KEEP-LOCKED", wt))
        elif wt.get("prunable") or not wt["path"].is_dir():
            # The checkout is gone; judge by the branch (or recorded HEAD) alone.
            if wt.get("branch"):
                merged = _is_merged(_git("rev-parse", wt["branch"]).strip(), primary)
            else:
                head = wt.get("head", "")
                merged = bool(head) and _is_merged(head, primary)
            out.append(("RETIRE" if merged else "STRAY", wt))
        elif wt["detached"]:
            head = _head(wt["path"])
            out.append(("RETIRE" if _is_merged(head, primary) else "STRAY", wt))
        elif _dirty(wt["path"]):
            out.append(("KEEP-DIRTY", wt))
        elif _is_merged(_git("rev-parse", wt["branch"]).strip(), primary):
            out.append(("RETIRE", wt))
        else:
            out.append(("KEEP-ACTIVE", wt))
    return out


def _merged_branches_without_worktree(primary: str) -> list[str]:
    in_use = {wt.get("branch") for _, wt in classify(primary) if wt.get("branch")}
    in_use = {b for b in in_use if b}
    names = []
    for ref in _git("for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines():
        if ref not in in_use and ref != primary and _is_merged(ref, primary):
            names.append(ref)
    return names


def _label(wt: dict) -> str:
    if wt["detached"]:
        return f"detached @{wt.get('head', _head(wt['path']) if wt['path'].is_dir() else '?')[:9]}"
    return wt.get("branch", "?") or "?"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--report", action="store_true",
                       help="list every worktree and exit 0")
    group.add_argument("--apply", action="store_true",
                       help="remove retirable worktrees and delete their merged branches")
    args = parser.parse_args()

    primary = _primary_branch()
    rows = classify(primary)

    if args.apply:
        for verdict, wt in rows:
            if verdict != "RETIRE":
                continue
            path = wt["path"]
            if path.is_dir():
                subprocess.run(["git", "worktree", "remove", str(path)],
                               cwd=REPO, check=True)
                print(f"removed worktree {path}")
            else:
                print(f"sweeping vanished worktree {path}")
            if wt.get("branch"):
                subprocess.run(["git", "branch", "-d", wt["branch"]],
                               cwd=REPO, check=True)
                print(f"deleted branch {wt['branch']}")
        subprocess.run(["git", "worktree", "prune"], cwd=REPO, check=True)
        return 0

    counts: dict[str, int] = {}
    for verdict, wt in rows:
        counts[verdict] = counts.get(verdict, 0) + 1
        print(f"{verdict:12} {wt['path']}  [{_label(wt)}]")

    orphans = _merged_branches_without_worktree(primary)
    if orphans:
        print(f"\n{len(orphans)} merged branch(es) hold no worktree "
              "(delete with `git branch -d <name>` if the name is spent):")
        for name in orphans:
            print(f"  {name}")

    if args.report:
        return 0
    retirable = counts.get("RETIRE", 0) + counts.get("STRAY", 0)
    if retirable:
        print(
            f"\nworktree hygiene: {retirable} retirable/stray worktree(s); "
            "run `python3 scripts/check_worktrees.py --apply` to retire them, "
            "or `--report` to review first"
        )
        return 1
    print("worktree hygiene: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
