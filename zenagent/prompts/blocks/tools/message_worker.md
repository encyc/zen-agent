Send a follow-up instruction to an existing worker. The worker keeps its own context; only send what changes or extends its task. Prefer it over spawning a new worker when the work is a continuation.
