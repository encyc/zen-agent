# ZenAgent Desktop (Tauri)

The zen-agent desktop frontend: a Tauri shell that drives `zen rpc` over
JSONL and mirrors the **dsh (DeepSeek Harness) desktop interaction
architecture** — three-column frame, event-sourced chat rendering, a
stateful composer, approval takeover, and the same design-token system.

```
desktop/
├── src/            React + TypeScript frontend (Vite, CSS modules)
│   ├── styles/     design tokens ported from dsh's ui-theme (1:1 names)
│   ├── rpc/        wire types + the id-correlated JSONL client
│   ├── state/      zustand stores: chat assembly, status, sessions, composer…
│   ├── i18n/       zh/en chrome strings
│   └── ui/
│       ├── AppFrame.tsx          three-column grid + drag handles (dsh AppFrame)
│       ├── columns.ts            dsh column solve (CENTER_MIN/SIDEBAR_*/RIGHTBAR_*)
│       ├── Sidebar.tsx           brand row + session browser + footer
│       ├── chat/                 ChatView, user bubble, assistant markdown,
│       │                         thinking row, tool card, notices, usage rows
│       ├── composer/             InputBar capsule, slash menu, docks, menus
│       ├── approval/             ApprovalPanel composer takeover
│       └── rightbar/             session inspector (usage/context/jobs/subagents)
└── src-tauri/      Rust shell: spawns `zen rpc`, bridges JSONL ↔ webview
    └── src/rpc.rs  process lifecycle, pending-request correlation, events
```

## Install

Download the file for your platform from the
[releases page](https://github.com/encyc/zen-agent/releases):

| Platform | File |
|---|---|
| macOS (Apple silicon) | `ZenAgent_<version>_aarch64.dmg` |
| macOS (Intel) | `ZenAgent_<version>_x64.dmg` |
| Windows (x64 / arm64) | `ZenAgent_<version>_<arch>-setup.exe` |
| Linux (x64 / arm64) | `.AppImage` (portable) or `.deb` |

Every installer carries its own `zen` runtime, so nothing else has to be
installed — the app you download is the whole agent. That also means the
runtime cannot be upgraded on its own: the shell and its sidecar always change
version together.

The builds are **not signed or notarized** yet:

- **macOS**: the first launch is blocked by Gatekeeper. Right-click the app →
  *Open*, or run `xattr -dr com.apple.quarantine /Applications/ZenAgent.app`.
- **Windows**: SmartScreen shows "Windows protected your PC" → *More info* →
  *Run anyway*.

Updates are checked in the background after launch (cached for an hour, silent
on failure) and can be turned off in Settings → Conversation. When a new
version exists, a banner above the chat offers *Update and restart*; it stays
disabled while a turn is running or an approval is waiting, because installing
restarts the app. The `.deb` cannot self-update — download the new package.

Only builds published with an update signature can self-update; a release whose
notes say "no update signature" must be downloaded manually.

## Architecture mapping (dsh → this app)

| dsh concept | Here |
|---|---|
| Electron shell + desktop-host | `src-tauri` Rust shell |
| Host spawn (`DesktopHostProcess`) | `rpc_start` command spawning `zen rpc --cwd <dir>` |
| `JsonRpcLineTransport` (JSONL, id-correlated) | `rpc_send` + pending map keyed by request id |
| `session.follow` snapshot + journal frames | `transcript` RPC snapshot + streamed agent events |
| `session.control` projections | `status` polling (2s) — zen rpc has no push channel |
| Cordis slot plugins | direct React components (no plugin runtime needed) |
| keyed node stores + selector subscriptions | zustand chat store mirroring `TranscriptBuilder` |
| `body[data-ds-dark-theme]` tokens | same attribute + same token names in `tokens.css` |
| ApprovalPanel composer takeover | same pattern, decision = `allow`/`always`/`deny` |
| Composer input machine (steer/queue) | Enter steers while running, Esc interrupts |

## RPC surface used

Requests: `prompt`, `cancel`, `steer`, `status`, `transcript`, `models`,
`commands`, `model`, `mode`, `approval`, `session` (op: `list`/`new`/`fork`/
`resume`/`delete`/`compact`/`undo`), `command` (48 built-ins), `shutdown`.

`models` / `commands` / `transcript` are additive methods added to
`zenagent/cli/rpc.py` for this frontend (the TUI reads them in-process).

## Develop

```bash
cd desktop
pnpm install
pnpm tauri dev      # full app: vite dev server + Rust shell + zen rpc child
pnpm build          # type-check + production frontend bundle
cd src-tauri && cargo check
```

The child binary is resolved as: `$ZEN_DESKTOP_BIN` → explicit `bin` option →
**the `zen` sitting next to this executable** (the bundled sidecar) → (debug
builds) the checkout's `.venv/bin/zen` → `~/.local/bin`, `/opt/homebrew/bin`,
`/usr/local/bin` → `$PATH`. A plain `pnpm tauri dev` therefore runs the
working-tree CLI, which carries the `transcript` / `models` / `commands` RPC
methods this frontend needs; set `ZEN_DESKTOP_BIN` to override it.

`cargo check` and `pnpm tauri dev` never need the sidecar: the
`bundle.externalBin` entry lives in `src-tauri/tauri.release.conf.json`, which
only the release workflow passes. Building a real installer locally means
building the runtime first:

```bash
uv run python scripts/build-sidecar.py          # -> src-tauri/binaries/zen-<triple>
cd desktop
pnpm tauri build --config src-tauri/tauri.release.conf.json
```

The default child flags are `--mode ask --no-mcp` (compatible with released
zen binaries); the mode chip switches plan/ask/zen at runtime. GUI apps
inherit launchd's minimal environment, so the shell spawns `zen rpc` with the
user's **login-shell environment** merged in (same approach as the dsh
desktop shell).

## Known gaps (v1)

- Pending queue rows are display-only: zen rpc has no promote/dequeue ops yet.
- The `+` attachment button falls back to drag-drop for real file paths (the
  webview cannot hand out paths from `<input type=file>`); the dialog plugin
  is wired but the picker result is not yet routed to image attachments.
- Menus render locally instead of delegating to `command` `screen` tokens.
- No shiki code highlighting yet; code blocks use the dsh typography ladder.
