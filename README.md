# ZenAgent

[![PyPI](https://img.shields.io/pypi/v/zenagent-py.svg)](https://pypi.org/project/zenagent-py/)
[![CI](https://github.com/encyc/zen-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/encyc/zen-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

A minimal coding agent written from scratch in pure Python: one interface for
streaming LLM calls, an agent loop that is an async generator, and a terminal
harness. No vendor SDKs, no agent framework — `httpx` is the only runtime
dependency of the kernel.

Named after the Zen of Python: explicit contracts, flat layers, and one obvious
way to do things. (Renamed from `pyagent`; there is no compatibility shim.)

Built as a learning project and as the small, hackable Python kernel that the
ecosystem is missing (in the spirit of `pi-ai` + `pi-agent-core`).

Repo: <https://github.com/encyc/zen-agent> · PyPI package: `zenagent-py` (imports as `zenagent`).

## Docs

Full documentation — architecture, usage guides, internals (source tour), contracts and the
feature map — lives in [`docs/`](docs/README.md). Start with [`docs/architecture.md`](docs/architecture.md).

## Install

```bash
# install the published CLI
uv tool install "zenagent-py[cli]"   # or: pip install "zenagent-py[cli]"
zen --help                          # imports as `zenagent`; needs Python 3.11+

# work on the project itself
uv sync --extra cli                 # kernel + interactive extras (prompt_toolkit, rich)
```

Requires Python 3.15+ (the project targets the current `uv` toolchain; any
3.11+ should work for the kernel itself).

## API keys

Read from the environment variable named by each provider in
`zenagent/catalog.yaml`:

| provider | env var |
|---|---|
| `zai-coding-cn` | `ZAI_CODING_CN_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `opencode-go` | `OPENCODE_API_KEY` |

## Usage

```bash
# one-shot, exits 0 on success and 1 when the provider failed
uv run zen -p "explain what this repo does"

# pick a model / reasoning level; allow write + execute tools
uv run zen -p "create /tmp/x/hello.txt, then read it back" -m deepseek-flash --yes --thinking high

# attach images (needs a vision model; deepseek-flash is one)
uv run zen -p "what is in this picture?" --image shot.png

# guardrails and reproducibility
uv run zen -p "..." --max-turns 20 --max-cost 0.05 --no-context-files
uv run zen -p "analyze this" --read-only --trace run.jsonl --cwd /path/to/project

# runtime modes: plan = read-only, ask = confirm (default), full = automatic
uv run zen -p "investigate and propose a plan" --mode plan
uv run zen -p "apply the fix" --mode full

# structured output (a single JSON object; no streamed text)
uv run zen -p "say hi" --json

# interactive REPL
uv run zen
```

Runtime modes: `plan` (read-only; the agent investigates and proposes a plan),
`ask` (default: write/execute tools need confirmation), `full` (run write/execute
tools without asking; catastrophic commands still need `--allow-dangerous`).
`--yes` means `full`, `--read-only` means `plan`, and `/mode plan|ask|full`
switches mid-session. Session display names come from the first question; rename
with `/rename <name>` (or `r` in the TUI session picker).

### Diagnostics

```bash
uv run zen doctor      # keys, catalog, config, session dir, terminal
```

### Extending the agent (file-based, no restart needed)

| What | Where | How it shows up |
|---|---|---|
| **Skills** | `.zenagent/skills/<name>/SKILL.md` (project) or `~/.zenagent/skills/` | listed in the prompt; the model loads one with `read_skill` |
| **Prompt templates** | `.zenagent/commands/<name>.md` | `/name args` expands and sends it |
| **Extensions** | `extensions = ["path.py"]` in config or `--extension` | registers tools, slash commands, prompt text, event hooks |
| **MCP servers** | `~/.zenagent/mcp.json` | tools exposed as `mcp__<server>__<tool>` |
| **Custom providers/models** | `[providers.custom.<id>]` in config.toml | usable with `-m <model>` |

Servers that are down at start are retried before each prompt (`/mcp` shows
state, `/mcp retry` forces it), and `/reload` re-discovers skills, templates and
extensions mid-session.

### Library / SDK

```python
from zenagent.agent import Agent
from zenagent.tools import ALL_TOOLS

agent = Agent(
    model=model,                 # a zenagent.providers.catalog.Model
    system_prompt="be brief",
    tools=list(ALL_TOOLS),       # or your own AgentTool objects
    api_key="...",
)
await agent.prompt("explain this repo")   # events via agent.subscribe(fn)
```

See `examples/` for a headless run, a custom tool with a fake stream (offline),
a direct MCP call, and a `zen rpc` client. For embedding with the full app layer
(MCP, skills, sessions), use `Runtime` + `ChatController` — see `examples/embed.py`.

### REPL commands

| command | effect |
|---|---|
| `/help` | list commands |
| `/exit` | leave (Ctrl+D also works) |
| `/clear` | forget the conversation |
| `/model [id]` | show / switch model (next turn) |
| `/thinking [level]` | `off` / `low` / `medium` / `high` / `max` |
| `/mode [plan\|ask\|full]` | show or switch the runtime permission mode |
| `/compact` | summarize the session into a new session file |
| `/undo` | drop the last turn (writes a new session file) |
| `/sessions` | list saved sessions (display name + filename) |
| `/rename <name>` | rename the current session |
| `/fork <n>` | branch a new session at user message n |
| `/import <path>` | load a session file and continue it |
| `/image <path>` | attach an image for the next prompt |
| `/skills` | list available skills |
| `/mcp` | MCP server status; `/mcp retry` reconnects |
| `/reload` | re-discover skills, templates, extensions |
| `/theme <name>` | `default` / `mono` / `solarized` / a JSON file |
| `/hotkeys` | show active keybindings |

Command arguments complete too (Tab): `/mode ` → plan/ask/full, `/thinking ` → levels,
`/model ` → catalog ids. In the TUI the same values appear as you type, and a bare
`/mode` or `/thinking` opens a selector.

While the agent is working, anything you type is queued as steering input
rather than starting a second run. Ctrl+C aborts the current run cooperatively.

## Configuration

Optional `~/.zenagent/config.toml` (themes, keybindings and MCP live in their own files):

```toml
default_model = "deepseek-flash"

[providers.deepseek]
base_url = "https://mirror.example/v1"   # optional
api_key_env = "MY_DEEPSEEK_KEY"          # optional

# define a provider from scratch
[providers.custom.local]
base_url = "http://127.0.0.1:8080/v1"
api_key_env = "LOCAL_API_KEY"
adapter = "openai-completions"
[[providers.custom.local.models]]
id = "my-model"
context_window = 32768
max_tokens = 4096
reasoning = false

extensions = ["~/work/my-extension.py"]

[tools]
allow = ["read_*", "grep"]     # never ask for these
deny = ["write_file"]          # always refuse
```

Themes: `~/.zenagent/keybindings.json` (`{"newline": "escape,enter"}`) and
`--theme mono`. MCP servers: `~/.zenagent/mcp.json` with
`{"servers": {"name": {"command": [...]} | {"url": "...", "headers": {...}}}}`.

Costs are estimated from the catalog's unit prices (input, output, and cache
read/write separately). DeepSeek bills peak/off-peak; the catalog has no time
dimension, so peak rates are used — treat `usage.cost` as a conservative upper
bound. Providers with subscription pricing (zai, opencode-go) carry 0.

Sessions are append-only JSONL under `~/.zenagent/sessions/`; `--resume` continues
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
`zenagent/providers/fixtures/` so the parser can be checked against the wire; the
replay tests skip when a capture is absent.

## Releasing

`zenagent-py` ships from a tag. Everything before the tag runs offline:

```bash
bash scripts/check.sh            # lint + tests
bash scripts/release-check.sh    # build sdist/wheel, install both ways, run the CLI
git tag vX.Y.Z
git push origin main vX.Y.Z      # the publish workflow re-runs the gate, then uploads
```

`release-check.sh` is the packaging gate: it asserts the version is consistent
(`pyproject.toml`, `__version__`, and the tag on HEAD), that the sdist and wheel
carry no dev or stray files, that a kernel-only install works (`pip install
zenagent-py`), and that a `[cli]` install starts (`zen --version`, TUI imports,
search providers, a settings round-trip in an isolated `$HOME`). It tests the
`requires-python` floor by default; pass `--python 3.13` for another
interpreter, or `--skip-build` to verify the existing `dist/`. It never uploads
and writes only under a temp dir.

`.github/workflows/publish.yml` runs the same gate before uploading. A manual run
is always a dry run — it builds and verifies but cannot publish; only a `v*` tag
push reaches PyPI:

```bash
gh workflow run publish.yml     # optional dry run on CI (build + verify only)
```

## Status

v1 (milestones 1–4), S1–S3 and the Textual TUI are complete; the offline suite
and recorded live captures are the gate (`docs/testing.md`). Implemented:
interactive REPL + TUI, MCP, skills/templates/extensions, RPC/SDK,
export/usage/telemetry, four protocol adapters, runtime modes
(`plan`/`ask`/`full`), session display names + rename, and TUI session
management (new/fork/delete/export/rename). Not yet: subagents and OS-level
sandboxing.
