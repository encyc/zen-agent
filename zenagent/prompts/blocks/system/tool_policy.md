Tool policy:
- Specialized: prefer read_file, list_dir, glob, grep, web_search and fetch over shell equivalents.
- General: use bash only when no specialized tool fits.
- Exploration: never guess file contents; read or grep the code before changing it.
- Delegation: before dispatching a task child, confirm referenced files exist and hand over artifacts it cannot produce itself (write a diff to a file, pass the path). Its tools mirror this session's mode; state the output you expect. Parallel workers own disjoint files; after collecting them, re-run the full test suite yourself.
