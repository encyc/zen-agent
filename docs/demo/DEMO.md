# Taiji Workers

A **taiji worker** is one unit of parallel work in the taiji ("太极") orchestration
layer. The identity is deliberately singular:

> task = git branch = git worktree = worker session

There is no separate task-state store that can drift from git.

## Anatomy

A worker consists of exactly three things:

1. **Branch** — `task/<slug>`, cut from the base branch when the worker is spawned.
2. **Worktree** — `~/.zenagent/worktrees/<project-key>/<slug>`, so every worker has
   its own checkout and the main worktree stays clean.
3. **Session** — a `zen rpc` child process (`--cwd <worktree> --yes`) plus its
   transcript. A turn is one short-lived child process that resumes the transcript,
   so a host restart costs a respawn, not the history.

Worker records live in the registry (`taiji.json` / `taiji.jsonl`) as runtime state;
on restart the registry is reconciled against `git worktree list`.

## Directed by the Router

The human talks to exactly one session — the Router. The Router spawns workers, sends
them messages, kills them, and decides when to merge. Workers use the full tool set
inside their own worktree and commit there; they never talk to the human. Each turn
end injects a one-line milestone back into the Router (`[worker task/<slug> · ok] <summary>`).

## Merged back on acceptance

When a worker's turn ends `ok`, the merge gate runs: the base branch must be checked
out and clean in the main worktree, then the branch is merged `--no-ff`, the worktree
is removed, and the worker becomes `merged`. A conflict aborts the merge and degrades
to an acceptance question — never a dangling `MERGE_HEAD` in the user's tree. In `zen`
mode acceptance happens without asking; in `ask` mode the Router asks first. Either
way the work lands only through a merge into the base branch.
