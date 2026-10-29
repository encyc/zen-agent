# pyagent

A minimal coding agent written from scratch in pure Python — a unified LLM
client, an agent loop runtime, and a line-based terminal harness. No vendor
SDKs, no agent framework: `httpx` is the only runtime dependency of the kernel.

Built as a learning project and as the small, hackable Python kernel that the
ecosystem is missing (in the spirit of `pi-ai` + `pi-agent-core`).

## Install

```bash
uv sync --extra cli        # kernel + interactive extras (prompt_toolkit, rich)
```

Requires Python 3.15+ (the project targets the current `uv` toolchain; any
3.11+ should work for the kernel itself).

## API keys

Read from the environment variable named by each provider in
`pyagent/catalog.yaml`:

| provider | env var |
|---|---|
| `zai-coding-cn` | `ZAI_CODING_CN_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `opencode-go` | `OPENCODE_API_KEY` |

## Usage

```bash
# one-shot, exits 0 on success and 1 when the provider failed
uv run pyagent -p "explain what this repo does"

# pick a model / reasoning level; allow write + execute tools
uv run pyagent -p "create /tmp/x/hello.txt, then read it back" -m deepseek-flash --yes --thinking high

# attach images (needs a vision model; deepseek-flash is one)
uv run pyagent -p "what is in this picture?" --image shot.png

# guardrails and reproducibility
uv run pyagent -p "..." --max-turns 20 --max-cost 0.05 --no-context-files

# structured output (a single JSON object; no streamed text)
uv run pyagent -p "say hi" --json

# interactive REPL
uv run pyagent
```

Read-only tools (`read_file`, `list_dir`, `glob`, `grep`) run without asking.
Write/execute tools (`write_file`, `edit_file`, `bash`) need `--yes`, an
interactive "yes", or an "always" answer for that tool in the session.

### Diagnostics

```bash
uv run pyagent doctor      # keys, catalog, config, session dir, terminal
```

### REPL commands

| command | effect |
|---|---|
| `/help` | list commands |
| `/exit` | leave (Ctrl+D also works) |
| `/clear` | forget the conversation |
| `/model [id]` | show / switch model (next turn) |
| `/thinking [level]` | `off` / `low` / `medium` / `high` / `max` |
| `/compact` | summarize the session into a new session file |
| `/undo` | drop the last turn (writes a new session file) |
| `/sessions` | list saved sessions |
| `/fork <n>` | branch a new session at user message n |
| `/import <path>` | load a session file and continue it |
| `/image <path>` | attach an image for the next prompt |

While the agent is working, anything you type is queued as steering input
rather than starting a second run. Ctrl+C aborts the current run cooperatively.

## Configuration

Optional `~/.pyagent/config.toml`:

```toml
default_model = "deepseek-flash"

[providers.deepseek]
base_url = "https://mirror.example/v1"   # optional
api_key_env = "MY_DEEPSEEK_KEY"          # optional
```

Costs are estimated from the catalog's unit prices (input, output, and cache
read/write separately). DeepSeek bills peak/off-peak; the catalog has no time
dimension, so peak rates are used — treat `usage.cost` as a conservative upper
bound. Providers with subscription pricing (zai, opencode-go) carry 0.

Sessions are append-only JSONL under `~/.pyagent/sessions/`; `--resume` continues
the most recent one. Destructive-looking operations never rewrite a file: `/undo`,
`/fork` and `/compact` all write a new session and leave the old one intact. The file format is exactly the public message model, so it
can be inspected with `jq` or replayed elsewhere.

## Architecture

```
cli/        REPL, headless entry point, rendering, permission gate, /compact   ← harness
agent/      loop (pure function) + Agent (state, events, steering, abort)      ← runtime
providers/  openai-completions (+ zai/deepseek dialects), anthropic-messages   ← client
            shared SSE transport, compat flags, packaged model catalog
types.py    messages, content blocks, usage                     ← vocabulary
events.py   stream events, agent events, abort token
```

Dependencies point strictly downward: `providers` knows nothing about `agent`,
`agent` knows nothing about `cli`. The kernel (`types`, `events`, `providers`,
`agent`, `tools`) imports only `httpx`; `prompt_toolkit`/`rich` live in the CLI
extra.

### Contracts worth knowing

- **`StreamFn` never raises.** Every failure (network, 4xx/5xx, parse error,
  cancellation) becomes a terminal `done`/`error` event. `safe_stream` enforces
  this for any stream function, including injected fakes.
- **Cancellation is a protocol state, not an exception.** The transport raises
  `Aborted` for cooperative cancellation; `asyncio.CancelledError` is reserved
  for real task cancellation.
- **Tools cannot crash the loop.** Unknown tools, invalid arguments, blocked
  calls, exceptions, and truncated (`stop_reason == "length"`) calls all become
  `is_error` tool results. Truncated calls are never executed and always get a
  result, so the transcript never carries a dangling `tool_call`.
- **Every event carries a `partial` snapshot**, so the UI never assembles deltas
  itself; assembly belongs to the adapter and happens once.

## Tools

`read_file`, `write_file`, `edit_file` (exactly-one-match replacement),
`list_dir`, `bash` (merged output, 30 KB head+tail truncation, process-group
kill on timeout/abort), `glob`, `grep` (ripgrep when available, Python fallback).

## Tests

```bash
bash scripts/check.sh     # the gate: sync + ruff + pytest (CI runs exactly this)
bash scripts/check.sh     # the gate: sync + ruff + pytest (CI runs exactly this)
uv run pytest -q          # fully offline: fake stream fns + replayed SSE fixtures
./scripts/eval.sh         # behavioral evals against a real model (opt-in, costs money)
```

Behavioral evals (real model, small cost) check the agent rather than the
code; they are separate from the offline gate:

```bash
./scripts/eval.sh --list                      # case ids
./scripts/eval.sh --model deepseek-flash --repeats 3
```

`scripts/capture_sse.py` records real provider traffic into
`pyagent/providers/fixtures/` so the parser can be checked against the wire; the
replay tests skip when a capture is absent.

## Status

Milestones 1–2 are complete and verified against a live provider; the
interactive harness and the second protocol adapter are implemented. Not in
scope (v1): MCP, subagents, sandboxing, a full-screen TUI, custom providers.
