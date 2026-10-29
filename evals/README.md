# Behavioral evals

Unit tests prove the code is correct. These cases prove the **agent** is useful:
each one runs a real headless turn in an isolated workspace and asserts on what
the model produced, which tools it used, why the run ended, what it cost, and
what it left on disk.

They call a real provider, so they are **not** part of `scripts/check.sh` (which
stays offline and key-free).

```bash
set -a && . ./.env && set +a
./scripts/eval.sh --list                              # case ids + prompts
./scripts/eval.sh --model deepseek-flash --filter read --repeats 1
./scripts/eval.sh --model deepseek-flash --repeats 3 --min-pass-rate 0.8
./scripts/eval.sh --json > /tmp/eval.json             # machine-readable report
```

Cost is tiny per case (fractions of a cent with DeepSeek); the current 18-case
run with 3 repeats is roughly $0.02 and a few minutes.

## Options for the S2 features

A case can exercise the harness extensions too:

| Option | Effect |
|---|---|
| `skills` | discover `.pyagent/skills` in the workspace (default: auto when present) |
| `extensions` | load extension modules (`["{{workspace}}/ext.py"]`) |
| `mcp` | start MCP servers (`{"echo": {"command": [...]}}`), tools appear as `mcp__echo__*` |
| `tool_deny` | fnmatch patterns the permission gate refuses |
| `read_only` | refuse every non-read tool |

Placeholders `{{python}}`, `{{fixtures}}` and `{{workspace}}` are substituted in
options, so MCP commands can point at the bundled test server.

Extra expectations: `tool_errors_contain` (assert a tool was refused with a
message) and `files_absent` (assert a write did **not** happen).

## Case format

One JSON file per case in `evals/cases/`:

```json
{
  "id": "read_file_secret",
  "prompt": "Read notes.txt and tell me the access code stored in it.",
  "setup": {
    "files": {"notes.txt": "access code: 4821\n"},
    "images": [{"path": "shot.png", "color": "red", "width": 64, "height": 64}]
  },
  "options": {"max_turns": 6, "thinking": "off", "context_files": false,
              "assume_yes": true, "allow_dangerous": false, "max_cost": 0.1},
  "expect": {
    "output_contains": ["4821"],
    "output_not_contains": ["I cannot"],
    "tools": ["read_file"],
    "tools_exclude": ["write_file"],
    "forbid_successful_commands": ["rm -rf /"],
    "termination": "stop",
    "max_cost": 0.05,
    "max_turns": 3,
    "files": {"out.txt": {"contains": "hi", "not_contains": "bye", "equals": "hi\n"}}
  }
}
```

| Field | Meaning |
|---|---|
| `setup.files` | text files created in the temp workspace |
| `setup.images` | solid-colour PNGs generated and attached (tests vision) |
| `options.thinking` | `off` by default (cheap, fast) |
| `options.context_files` | load `AGENTS.md` from the workspace (A/B-testable) |
| `options.max_turns` / `max_cost` | guardrails for the run |
| `expect.tools` | **ordered subsequence** of tool names (extra calls allowed) |
| `expect.forbid_successful_commands` | a `bash` call containing this substring must not succeed |
| `expect.files` | filesystem side effects, checked before the workspace is removed |

Assertions are case-insensitive on output text (models vary capitalisation).

## Failure kinds

The report labels each failure so regressions are diagnosable at a glance:

- `tool_use` — expected tool not used, or a forbidden one was
- `wrong_answer` — output content assertion failed
- `over_budget` — exceeded `expect.max_cost`
- `max_turns` / `max_cost` — hit the configured guardrail
- `provider_error` — the run ended with a provider error

## Rule: assert the outcome, not the route

A model may solve a task with any tool, and that is fine. Two kinds of cases,
and they must not be mixed:

- **Outcome cases** — assert only what the user can observe (files, output,
  termination, cost). Never assert a tool sequence: a correct bash-only solution
  is still correct.
- **Tool-routing cases** — when the tool *is* the behaviour under test, say so in
  the prompt ("Use the grep tool to ...") and then assert `expect.tools`. An
  implicit tool expectation punishes a valid alternative path and shows up as a
  flaky eval.

`multi_step_investigation` was flaky for exactly this reason: it asserted
`read_file` while the model sometimes fixed the bug with `bash`, `sed`, or
`list_dir` + `bash`.

## Adding a case

1. Pick a behaviour a real user would notice (not an implementation detail).
2. Give it an isolated workspace and the smallest prompt that triggers it.
3. Assert the **observable** outcome: output, tool use, files, cost — not the
   exact wording.
4. Run it with `--repeats 3`; if it is flaky, tighten the prompt or loosen the
   assertion until it is stable (flaky evals are worse than none).
