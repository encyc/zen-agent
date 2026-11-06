# Desktop UI Full Parity with pi-desktop — Stage 2 Design

**Status:** direction approved by the user ("全面对标pi")
**Date:** 2026-11-06
**Reference:** `/Users/mac/pi-desktop` v0.21.0 (read-only). pi-desktop is LGPL-3.0: measurements and rendered behavior are the reference; no source is copied.
**Bound:** all behavior is backed by the existing zen RPC (`prompt, cancel, status, shutdown, steer, tools, models, commands, transcript, model, mode, usage, approval, session, command`) plus existing Tauri commands (`rpc_start/rpc_send/rpc_stop`, dialog plugin). No new server ops.

## Intent

Stage 1 aligned tokens and added interactions, but the shell's information architecture still followed our old scope model. The user judged the result unlike pi. This stage rebuilds the primary surfaces structurally, so the app reads as the same product: full-height sidebar with `置顶 / 会话 / 项目`, 46px chrome, pi's transcript anatomy, pi's composer plate, pi's home, and pi's work-panel chrome.

## Goals

1. **Structural parity**: same regions, order, dimensions and interaction model as pi for the shell, sidebar, topbar, home, transcript, composer, and work panel.
2. **No dead affordances**: every control either works on existing RPC/local state or is absent.
3. **Preserve Stage 1 guarantees**: fail-closed cross-project safety, capture-phase Escape handling, keyboard reachability, silent rename/pin success.
4. **Tests first**: unit tests for grouping/section/turn logic; Playwright e2e over the mocked bridge for each surface; typecheck and build green.

## Non-goals / backend-gated exclusions

These require data the RPC does not expose. They are **not rendered**, to avoid dead UI:

- Plugins marketplace, scheduled tasks, provider/API-key settings, remote hosts, worktrees, voice, computer use.
- File tree / file viewer / diff fetch in the work panel (only per-tool `details.diff`).
- Server-side message search (⌘K remains session + command search).
- Per-session running badges and notification feeds from the backend (local action notices only).
- Multi-select batch session ops, drag-reorder, radial menu, @ file references, non-image attachments.

## Parity decisions

1. **Always list all sessions** (`session op=list scope=all`). The `当前项目/全部` toggle is removed. Sessions with an empty `project` render under `会话`; the rest render under `项目` as collapsible groups. Pinned sessions render first under `置顶`.
2. **Cross-project rows stay visible but disabled** (fail-closed), with an explanation. A project header toggles collapse only. Switching projects happens through the explicit `打开项目目录…` action, which restarts the RPC process with the chosen cwd via existing `rpc_start` after confirmation; the current run ends.
3. **Composer lives inside the main pane**, pinned to its bottom, never spanning the sidebar or work panel. Its measured height drives the transcript's bottom reserve.
4. **Footer**: Settings, Tools, Notifications, version. Settings and Tools are real pages; Notifications is a local store fed by action errors and notices. No plugin/scheduled icons.
5. **Work panel**: 46px header with two tabs (`检查器`, `上下文`), resize handle, fixed 28px toggle at top-right, min 244 / default 360 / center floor 450. No file tree.
6. **Home** is shared by the pre-connect and empty-session states: 100px mark, 28px headline with an inline project picker, onboarding tips, composer pinned at the bottom. Returning users auto-connect to `zen.last.cwd`; first run keeps the folder picker.
7. **Transcript** groups each turn and folds tool steps behind `已处理 {duration} · {n} 个步骤`, with pi's row anatomy, 26px minimap rail, and drag handles for the 560–900px content band.
8. **Topbar** is 46px: folder icon + project + `/` + title; right side only new-session and ⌘K search; the work-panel toggle is the fixed viewport control.
9. **Settings** are local (`localStorage`): theme, language, session time, font scale (drives `--ds-font-scale`), content width. Tools lists the existing `tools` RPC result. Version comes from the package version injected at build.

## Visual values adopted from pi

| Surface | Values |
|---|---|
| Chrome | toolbar/topbar/sidebar header 46px; sidebar footer ≥40px (padding 7px 0 2px); sidebar body padding 0 8px 6px |
| Sidebar | clamp(240, 275, min(520, 100vw − 320)); rows min-height 28; group header 28; standalone list max-height 146; pinned max-height min(233px, 30vh); 10 sessions per project then `加载更多` |
| Conversation | band 760px (min 560, gutter 24); padding 20px 32px, top 46px; row padding 12px 0, gap 12; user bubble radius 18 (bottom-right 8), padding 10px 15px, 14px/1.55, primary-text 8%; assistant 14px/1.6, meta 11px; thinking 12.5px; tool row 24px, expanded mono 14px/1.6 max-height 260 |
| Composer | plate min(100%, 768px), radius 20, pi composer shadow, input padding 10px 12px 0, min 3lh / max 7lh, toolbar padding 4px 8px 8px, chips 20px, send 28px |
| Work panel | header 46; tabs 28px high; toggle 28px at top 9 / right 12; panel z-index 20 |
| Home | stack 760, padding 28px 12px 24px, gap 16; mark 100px; headline 28px normal, max 42rem; tips card max 560px |
| Radii / type | 4/6/8/10/12/14/16/18/20/full; 10.5/11/11.5/12/12.5/13/13.5/14/15/16/18/28; chat leading 1.55 |

## Approaches

**A. Cosmetic-only (rejected).** Keeps the scope model and restyles. Cheaper, but the user's complaint is the structure; it would still not read as pi.

**B. Structural rewrite in our codebase (chosen).** Reimplement the shell, sidebar, transcript grouping, composer dock, home and work-panel chrome with our own components and CSS Modules, using pi's measurements and behavior. Keeps our state/RPC architecture and Stage 1 safety fixes.

**C. Port pi components (rejected).** LGPL source copying, Tailwind/plugin assumptions, and a different state model; high risk and license burden.

## Risks

- Moving the composer into the main pane touches scroll/follow-tail behavior; keep the Stage 1 follow logic and verify with e2e.
- Reconstructing turns from the flat node stream can mis-group interrupted or replayed sessions; pin behavior with unit tests for user boundaries, running turns, and compactions.
- Restarting the RPC for project switching ends the current run; require an explicit confirmation and never auto-switch.
- Removing the scope toggle changes e2e assumptions from Stage 1; update them with the implementation.
