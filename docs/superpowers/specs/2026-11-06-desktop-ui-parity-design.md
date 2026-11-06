# Desktop UI Parity with pi-desktop — Design

**Status:** draft, awaiting approval
**Date:** 2026-11-06
**Reference:** `/Users/mac/pi-desktop` v0.21.0, used read-only for visual and interaction reference. pi-desktop is LGPL-3.0: no source is copied into this repository.
**Bound:** every capability below must be backed by the existing zen RPC. No new server ops, no new run/session semantics.

## Intent and why

Our Tauri shell already mirrors pi-desktop's structure — three columns, session sidebar, transcript, composer, approval takeover, pending docks. Daily use still falls short in two ways: the visual language is close but not the same (density, type ramp, radii, chrome), and several high-frequency interactions are missing (⌘K palette, session rename/pin/grouping, complete queue affordances, folded turn process, a usable right panel, preferences).

This project closes that gap in four vertical slices. Each slice delivers the visual and interaction parity of one surface, stays inside the RPC capabilities that exist today, and ships on its own with tests.

## Goals

1. **Visual parity.** Adopt pi-desktop's type ramp, radii, spacing, neutral palette, sidebar width, and 760px conversation band through a token parity layer, in both dark and light themes.
2. **Interaction parity.** Add the high-frequency interactions pi-desktop has that our RPC already supports: ⌘K palette, session rename/pin/group/scope/import/export/undo/compact, queue send-now/take-back/clear-all, folded turn process, tool details, turn changes and usage, right-panel tabs, and preferences.
3. **No backend semantics invented.** Every action maps to an existing request, session op, or slash command.
4. **Each stage independently shippable**, with unit tests, Playwright e2e over the mocked bridge, and a green repository gate.

## Non-goals

- Server-side session or message search; `@` file references; non-image attachments.
- Per-item queue remove, edit, or reorder; queue move up/down; send-now other than promote-to-steer.
- Files/Review tabs, file tree, file viewer, or diff fetch in the right panel.
- Structured plan-review or ask-tool cards; permission mode separate from plan/ask/zen.
- Worktrees, plugins, voice, computer use, scheduled tasks, remote hosts, provider/API-key settings, subagent transcripts.
- Conversation minimap, radial menu, multi-select batch session ops, drag-reorder of work-panel tabs.
- Adopting Tailwind or a component library; verbatim copying of pi-desktop source.

## Anti-goals

- A reskin that regresses IME behavior, keyboard focus, selectable text, or transcript scroll/follow-tail.
- Affordances that look enabled but have no RPC behind them.
- A second styling system running beside CSS Modules.

## Constraints

- Requests available today: `prompt, cancel, status, shutdown, steer, tools, models, commands, transcript, model, mode, usage, approval, session, command`.
- Slash commands are dispatched as `{type:'command', name, argument}` and return `{message, screen, target, show_help, reset_transcript, quit, appearance, language, model}`. The desktop currently ignores `screen/target/show_help`; this project does not build a terminal-screen renderer.
- `rename` and `pin` act on the **current session only** (`/rename <title>`, `/pin on|off`).
- `session op=list` returns a project display name but no project root path, and the RPC process is bound to one `--cwd`. Resuming a session from another project would leave tool execution in the old project. **Cross-project sessions are therefore discovery-only**: they appear under the all scope and in palette results with their project label, but selecting one is disabled with an explanation.
- `session op=import` only switches to the chosen transcript file; it does not adopt the file into the current project. The action is labelled "open session file", not "import into project".
- Queue ops are limited to `promote(pending_id)`, `dequeue` (latest queue entry only), `clear_pending` (both lanes).
- No file-list, file-read, or file-diff RPC; only per-tool `details.diff` from edit/write tool calls.
- Tauri v2 + React 18 + strict TypeScript + CSS Modules; `noUnusedLocals`/`noUnusedParameters`.
- pi-desktop license: reference the rendered design and measurements; reimplement in our own components and CSS modules.

## Capability map

| Surface | pi-desktop interaction | Backing capability | Verdict |
|---|---|---|---|
| Palette | ⌘K search sessions + commands | `session op=list`, `commands` | Client-side search over title/name + command names |
| Session rename | inline any row | `/rename` current only | Current row + topbar only |
| Session pin | any row | `/pin` current only | Current row only; pinned group already renders |
| Session scope | project/all | `session op=list {scope}` | Full for listing and search; a recorded project root lets a row restart the runtime there, then resume |
| Import/export | dialogs | `session op=import/export` | Open an external file / export md or html; no adoption into a project |
| Undo/compact | menu actions | `session op=undo/compact` | Full, wire the dead store actions |
| Delete | armed two-step | `session op=delete` | Armed confirm client-side |
| Queue | edit/remove/reorder/send now | promote/dequeue/clear | Partial: send now, take back latest, clear all |
| Attachments | images + files | images only | Images only |
| `@` file refs | composer completion | none | Not built |
| Permissions | ask/accept-edits/auto | plan/ask/zen modes | Mode picker only |
| Transcript process | folded per-turn process | transcript rows + tool details | Full client-side |
| Turn changes | changed-file summary | `details.diff` | Full client-side |
| Right panel | Files/Review/Plugins | none | Overview/Todos/Jobs/Subagents only |
| Preferences | full settings | theme/lang/model client state | Client-only preferences |
| Plan/ask cards | structured approval | none | Not built |

## Approaches considered

**A. Vertical slices (chosen).** Each slice delivers visual + interaction parity for one surface, in frequency order: shell/sessions/palette → composer → transcript → right panel/preferences. Every component is touched once, every slice is demoable, and capability gaps stay visible per surface.

**B. Visual first, interaction second.** Fastest path to "looks like pi-desktop", but every component is restyled and then rewired; the app would look right while feeling wrong for a whole stage.

**C. Interaction first, visual second.** Early functional gains, but the visual alignment the user explicitly asked for lands last, and interactions styled against the old tokens need re-polishing.

A wins because the request is explicitly "both, as much as possible"; a vertical slice is the only order that delivers both per surface without double-touching components.

## Visual parity layer

A new `--ds-*` parity block is added to `desktop/src/styles/tokens.css`, and the existing `--dsw-*` aliases are re-pointed at it. Components keep using `--dsw-*`, so the parity layer can be tuned without touching every module.

Values taken from pi-desktop (`apps/desktop/src/styles/tokens.css`, `chat-shell.css`):

- Font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`.
- Type ramp with `--font-scale`: 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 14 / 15 / 16 / 18 / 20 / 28px.
- Radii: 4 / 6 / 8 / 10 / 12 / 14 / 16 / 18 / 20 / 24 / full.
- Sidebar width: `clamp(240px, 275px, min(520px, calc(100vw - 320px)))`; existing drag/persist behavior stays.
- Conversation and composer band: 760px, resizable within 560–760px (stage 4 preference).
- Neutral gray palette and white-alpha text tiers, dark default plus light override; accent stays monochrome.
- Chrome: 40px conversation topbar, 20px composer radius, tile/raised surfaces instead of borders where pi-desktop uses tonal layers.

CSS Modules stay; no Tailwind. Component CSS consumes parity tokens and the shared primitives.

## Stage 1 — Shell, sessions, ⌘K palette

**Topbar.** Replace `TopStrip` with a conversation topbar: macOS traffic-light inset, project · session title (inline rename on the current session), new-session button, search button (⌘K), right-panel toggle. The whole bar stays a Tauri drag region except its controls.

**Sidebar.** Add a project/all scope toggle (`session op=list`), pinned section, project groups (group by the session `project` field), the existing text filter, and per-row hover menu. Rows show title, last-reply age, model, pending/running cue. The hover menu offers rename and pin on the current row only, fork on the current row (existing rule), and armed delete everywhere. Import/export and preferences/theme live in the footer.

**Session actions.** Rename via `command {name:'rename', argument}`; pin toggles via `command {name:'pin', argument:'on'|'off'}`; import uses the native open dialog then `session op=import`; export uses the native save dialog then `session op=export`; undo and compact are topbar menu actions; delete is a two-step armed row action. All errors surface in the existing error banner, never a silent catch.

**⌘K palette.** One overlay with a query field and two result groups: sessions (client-side match over title/name/project) and commands. A session with a recorded project root resumes on Enter; when that root differs from the active project the runtime restarts there first (pi's `selectSession → activateProject`). Only legacy rows with no recorded root are listed and disabled with a hint. Commands follow an allowlist policy: zero-argument, non-destructive commands run directly; commands that need arguments insert `/name ` into the composer instead of running bare; destructive or screen-only commands (`quit`, `exit`, `clear`, `settings`, `sessions`, `model`, `mode`, `theme`, `lang`, `thinking`) are excluded. Arrow keys move, Enter activates, Esc closes. No server search, no terminal screens.

**Tests.** Unit tests for grouping, scope filtering, sort order (pinned first, then updated desc), palette matching, and armed-delete state. E2E using the existing fake bridge: palette opens and resumes a session, rename calls the command with the typed title, pin calls `pin on`, delete needs two clicks, scope toggle sends `scope:'all'`. Build stays green.

## Stage 2 — Composer and shortcuts

**Toolbar.** Align the composer chrome with pi-desktop: attach, mode, model, thinking control as a compact slider/segmented control over the existing levels, context ring opening an inspector popover (context used/window, cache hit, turn time, cost), send ⇄ stop. Image chips and the native picker stay.

**Queue dock.** Keep the lane pills, align copy and order with pi-desktop: send now = promote (running, text-only), take back latest = dequeue (newest headline), clear all = both lanes. Disabled states mirror capability, not appearance.

**Slash menu.** Show command descriptions from the catalog; argument hints are not exposed over RPC, so none are shown.

**Shortcuts.** Central shortcut module: ⌘K palette, ⌘N new session, ⌘B sidebar, ⌘J right panel, ⌘. cancel, ⌘, preferences, Enter submit / Shift+Enter newline / Enter-while-running steer, Esc cancel or close overlay. Documented in preferences.

**Not built:** `@` file refs, queue reorder/edit, permission mode axis.

## Stage 3 — Transcript and turn actions

**Process folding.** A turn runs from one user message to the next. Each assistant node is split into a thinking item and a text item; thinking items and tool rows are grouped into one collapsible process block, while assistant text stays in the normal flow so answers are never hidden. The block is open while the turn runs and collapsed after it finishes, summarised as "思考 12s · 5 个工具". Duration appears only when `thinking_seconds` is present (live runs); history replay omits it rather than inventing a value. Failed or interrupted tools force the block open.

**Tool cards.** Render fields already present in `details` but ignored today: exit codes, todo phases, task cost/tokens. Keep the existing diff card and output caps.

**Turn changes.** One changed-file summary per turn, aggregated from tool diffs, with per-file and total ± counts; clicking a file keeps the existing inline diff behavior.

**Usage.** A per-turn usage capsule (input/output/cache/cost, duration) from existing message and status fields.

**Actions.** Copy message, copy conversation, regenerate (existing), undo last turn (`session op=undo`, current session, not busy). Jump-to-latest stays.

**Not built:** minimap, radial menu, plan/ask cards, message edit/revision pager.

## Stage 4 — Right panel, preferences, welcome

**Right panel.** Keep the resizable column; replace the single inspector with tabs: Overview (context/usage/todos), Jobs (background jobs), Subagents. Maximize/restore, Alt+J toggle, open from the topbar. No Files/Review tabs.

**Preferences.** A client-only overlay reached from ⌘, and the sidebar footer: theme system/light/dark, language zh/en, font scale (0.85/1/1.15/1.25), conversation width (560–760px), and the shortcut reference. Stored in `localStorage`; no provider keys or server settings. Changing the language also sends `/lang <code>` and re-fetches the command catalog, because command descriptions and result messages are localized by the server; already-rendered text is not retroactively translated. If `/lang` fails, the client locale is reverted and the error is shown.

**Welcome/home.** Align the connection/home screen visually with pi-desktop's empty home: brand mark, current-directory picker, recent directories/sessions, and a home composer variant where the RPC is already connected. Onboarding hints derive from `models` availability; no API-key editor.

**Theme.** System follows `matchMedia`; existing dark override attributes stay.

## State and data design

- New pure helpers: `sessionGroups(sessions, scope, query)`, `paletteResults(sessions, commands, query)`, `nextTheme`, `clampContentWidth`, `groupTurnProcess(messages)`.
- New stores: palette (open, query), preferences (theme, locale, fontScale, contentWidth), sidebar scope (persisted).
- Armed delete stays local to the row; no global confirmation state.
- No changes to `rpc/types.ts`; all new UI reads existing fields.

## Error handling

- Session action failures render the returned error message in the existing banner; no silent `catch(() => {})` for user-initiated actions.
- Cancelled dialogs and empty rename input are no-ops.
- Palette and preferences never send unsupported requests.

## Testing and acceptance

- Unit tests per helper, written before implementation.
- E2E extends the fake bridge for `command`, scoped `list`, `undo`, `compact`, `import`, `export`; covers palette, rename/pin/delete, queue, process folding, preferences, and panel tabs.
- `pnpm test`, `pnpm build`, `pnpm e2e`, and `bash scripts/check.sh` stay green after every stage.
- Manual acceptance: both apps side by side at 1280×820 in dark and light, checking chrome, type ramp, sidebar density, composer, transcript, and panel per stage.

## Risks

- **License.** pi-desktop is LGPL-3.0; only rendered behavior and measurements are referenced. A reviewer checks no verbatim source lands.
- **Visual drift.** Copying values without pi-desktop's Tailwind scales can still look off; the parity-token layer and side-by-side check are the mitigation.
- **Over-folding.** Collapsing process by default can hide errors; failed tools force the block open.
- **Current-only rename/pin** can read as broken on other rows; the menu shows those actions only on the current row.
- **Palette expectations.** Users may expect message search; the placeholder copy states it searches sessions and commands.

## Calls made in this design

- Parity tokens alias the existing `--dsw-*` layer instead of rewriting component styling.
- No Tailwind, no new dependencies for this project.
- Stage 1 ships first; each stage gets its own full plan.
- Preferences and palette are client-only.
- Rename/pin are exposed only where the RPC supports them.
- Terminal screens (`screen/target/show_help`) remain unrendered.
- All-scope sessions carry `project_path`; opening one in another project restarts the cwd-bound runtime at that root before resuming. Legacy rows with no recorded root stay discovery-only.
- Import opens a transcript file; it is not an adoption flow.
- The palette runs only allowlisted zero-argument commands; argument commands insert their prefix into the composer.
- Thinking is folded per assistant node, not per row; assistant text is never collapsed, and history omits thinking duration.
- Font scale and the type ramp are adopted surface by surface, converting each stage's modules to type/space tokens. Untouched modules keep their px values until their stage; no global lint gate is added now.
- Export offers md or html through the native save dialog; the backend appends nothing the UI can choose.

## What is left to the builder

- Component decomposition and class names inside the stated surfaces.
- Exact copy for new strings in both locales.
- Pixel-level tuning against the running pi-desktop reference.
