# Examples

Small, runnable programs -- one per way of using the library. Each is checked by
`tests/test_examples.py`, so an example that stops working fails CI.

| file | shows | model call | needs a key |
|---|---|---|---|
| `headless.py` | `run_headless()`: one prompt, streaming, exactly like `zenagent -p` | real | yes |
| `embed.py` | `Runtime` + `ChatController`: the full app layer (MCP, skills, sessions, permission gate) inside your own program | real | yes |
| `rpc_client.py` | the `zenagent rpc` JSONL wire protocol from the client side | real | yes |
| `custom_tool.py` | an `AgentTool` plus a fake `StreamFn`: the real agent loop, fully offline | fake | no |
| `mcp.py` | starting an MCP server and calling one of its tools directly | none | no |

```bash
uv run python examples/custom_tool.py            # offline, no key needed
uv run python examples/mcp.py                    # offline (needs a source checkout: uses tests/fixtures)
DEEPSEEK_API_KEY=... uv run python examples/headless.py "say hi"
DEEPSEEK_API_KEY=... uv run python examples/embed.py "explain this repo"
DEEPSEEK_API_KEY=... uv run python examples/rpc_client.py "say hi"
```

## Which one do I want?

- **One prompt, done.** `headless.py`. It goes through `run_headless`, so the
  permission gate, context files and session persistence behave exactly as they
  do in the CLI.
- **A longer conversation, or the whole app.** `embed.py`. Build the `Runtime`
  once; every `chat.submit()` is another turn and `chat.transcript` is a plain
  view model you render however you like.
- **A different process, or a non-Python frontend.** `rpc_client.py`.
- **Your own tool, or offline tests.** `custom_tool.py`. The fake stream is the
  same technique the test suite uses (`docs/testing.md`).
- **MCP server tooling.** `mcp.py`.

## Two things to keep in mind

- Constructing `Agent(...)` directly, as `custom_tool.py` does, skips the harness
  layers: no permission gate, no context files, no session. That is fine for a
  demo with a fake stream; for real work go through `run_headless` or `Runtime`.
- `mcp.py` points at `tests/fixtures/mcp_echo_server.py`, so it only runs from a
  source checkout. Swap in any command or URL for a real server.
