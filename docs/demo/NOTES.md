# Notes: How Worktrees Isolate Concurrent Work

- **Separate working directories, one repository.** `git worktree add` checks out a branch into its own directory, so each task gets an isolated filesystem tree while still sharing the single object database and refs under `.git` — no duplicated clone, no divergent history.
- **Branch isolation is enforced by checkout.** A branch can only be checked out in one worktree at a time, so two agents cannot silently edit the same branch; concurrent work must live on distinct branches and therefore distinct directories.
- **Uncommitted changes cannot leak across tasks.** Edits, staged files, and index state live in the worktree and its own `.git/worktrees/<name>` metadata, so an agent's dirty working state never spills into another task's tree.
- **Build and tool state stay contained.** Caches, `node_modules`, virtualenvs, and generated artifacts are per-directory, so parallel runs do not clobber each other's installs or outputs — and nothing needs stashing to switch tasks.
- **Coordination happens at the commit boundary.** Worktrees converge only deliberately, via commit and merge/rebase on the shared refs; parallel workers stay disjoint until someone reviews and integrates, which makes conflicts explicit and reviewable instead of silent.
