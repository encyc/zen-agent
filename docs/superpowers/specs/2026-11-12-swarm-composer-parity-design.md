# Swarm Composer Parity — Design Spec

**Date:** 2026-11-12
**Status:** Draft for approval
**Branch (at execution):** a new worktree off `main`
**Builds on:** the shared composer plate already on `main` (`4d11eb8`, "one composer plate across coding, zen and swarm").

## 1. Problem

The plate is shared, so the room composer no longer looks different from coding/zen. What is still missing is capability: the room can only `post {text}`. Everything a coding composer does beyond sending text — quoting context, attaching images, `/` commands — has no room-side equivalent.

"Parity" therefore cannot mean "copy the coding controls". Coding's controls all point at one session; the room has none. Parity means **the same controls, with each control's referent defined in room terms**.

## 2. The room model (agreed)

- The room is always open. The user keeps `@`-ing roles about different things; roles reply or `@` each other.
- Several tasks run in parallel across roles; requests to a busy role queue (per-role serial scheduling, [manager.py:606](../../../zenagent/app/swarm/manager.py)).
- **The composer belongs to the room, not to any task.** There is no "current session" to bind to.

Two alternatives were rejected in conversation:

- **Focus-session composer.** Binding the composer to a selected task/role and routing model, context, steer and stop through it. Rejected: it forces a focus switch before every second `@`, which is the exact behaviour this work removes.
- **Task-level steer.** Injecting a message into a running task. Rejected for now: the user's actual need — "I'm adding context to something already said" — is served by quoting. Steering a live run would add a wire op and a race (queue vs. in-flight) with no user-visible need left over.

## 3. Goals

1. **Quote** — any room message can be quoted; the quote travels with the new message into every task it spawns.
2. **Attachments** — images on a room message, fanned out to the tasks that message spawns.
3. **Room command table** — a small, honest `/` vocabulary that maps onto existing ops.
4. **Model / thinking** — read-only, shown per target role; never a room-level override.
5. **Organization unchanged** — roles, `@`-dispatch, task registry, chains.

## 4. Non-goals and anti-goals

- No per-message model/thinking override.
- No task-level steer or interrupt.
- No room-level context meter (there is no single context to meter).
- No change to plate geometry, resize, auto-grow, token boxes or key handling.
- **Anti-goal:** a control that looks identical to coding's but silently acts on an arbitrary task. Every control either has an honest room referent or is absent.

## 5. Constraints from the current code

- `SwarmManager.post(text)` only ([manager.py:256](../../../zenagent/app/swarm/manager.py)); the wire op mirrors it ([ops_swarm.py:65](../../../zenagent/cli/rpc/ops_swarm.py)).
- `RoomMessage` has no reference or attachment fields ([room.py:40](../../../zenagent/app/swarm/room.py)); `_message_record` is the persisted/wire shape ([room.py:270](../../../zenagent/app/swarm/room.py)).
- A task's entire prompt is built from `message.text` plus metadata ([protocol.py:22](../../../zenagent/app/swarm/protocol.py)); there is no path for extra content today.
- `Agent.prompt(text, images)` already accepts image blocks ([agent.py:191](../../../zenagent/agent/agent.py)). Coding loads paths and refuses when the model cannot take images ([server.py:733](../../../zenagent/cli/rpc/server.py)).
- Per-task cancel/retry already exist in the task list UI ([SwarmTasks.tsx:61](../../../desktop/src/ui/SwarmTasks.tsx)), so the composer does not need a stop button.
- The room log is JSONL; every new field must be optional so existing lines still load.

## 6. Design

### 6.1 Quote

**Data.** `RoomMessage.ref: Ref | None`, where `Ref = {messageId, authorKind, authorId, text}`. The referenced text is snapshotted at post time so a task prompt is self-contained and the quote card renders even when the source message is outside the loaded window.

**Wire.**

- Request: `{type:'swarm', op:'post', text, ref?: messageId}`.
- Message records (push and `messages` op) gain `ref` when present.
- Validation: an unknown id is refused (`swarm_invalid`, "unknown ref") rather than silently dropped. System notices are not quotable.

**Prompt.** `injected_prompt` renders the quote before the user's own text, so the agent can tell context from instruction:

```
Quoted {authorKind}:{authorId} ({messageId}):
> first line
> …

Message:
<user text>
```

**UI.**

- Each message row gets a quiet "引用" action (hover). Selecting it stores the quote in the composer.
- The quote and the attached images are keyed **per project**, next to the room draft. A quote taken in room A must never ride along into room B, where its id would be refused.
- A quote strip above the plate shows author, two lines of text and `×`. Escape clears it when the draft is empty.
- The sent message renders a quote card; clicking it scrolls to the source and flashes the row.
- The quote belongs to the message, not to a role: every task admitted from that message carries the same quote. `@` still decides who runs.

### 6.2 Attachments

- `swarm post` gains `images: string[]` (absolute paths, the same shape `prompt` takes — [appStore.ts:698](../../../desktop/src/state/appStore.ts)).
- `RoomMessage.images: list[str]`, optional, default empty.
- **Admission check:** every mentioned role's model must accept images. If any does not, the whole post is refused, naming the role — the same stance coding takes, with no silent text-only fallback.
- **Runner:** loads the paths once per task (extract the reader from [server.py:733](../../../zenagent/cli/rpc/server.py)), passes `images=` to `Agent.prompt`, and lists the file names in the prompt text so transcripts stay readable.
- **Fan-out:** every task from that message gets the full set. `k` tasks means `k×` tokens; that is the honest cost of a room message and is surfaced before send (6.5).
- **UI:** attach button and drag-drop reuse `ComposerAttachments`. `attachDragDrop`, currently wired inside `InputBar` ([InputBar.tsx:143](../../../desktop/src/ui/composer/InputBar.tsx)), moves into the plate (or a shared hook) so both adapters inherit it.
- **Paste is out of scope.** No adapter has a clipboard-image path today, and getting bytes from the clipboard into a file needs new native work. Parity means matching coding, not exceeding it.
- **Feed:** attachments render as file-name chips, the same as coding. No thumbnails and no Tauri asset protocol.
- Images with no `@` cannot be sent: the send control is disabled with "图片需要 @ 一个角色". Text with no `@` remains allowed — that is the room's "say something" path.

### 6.3 Room command table

Only these appear in the room's `/` menu:

| Command | Effect | Backing |
|---|---|---|
| `/reload` | re-read role manifests from disk | existing `reload` op |
| `/roles` | open the roles panel | UI only |
| `/reset <role>` | reset one role's session | existing `reset` op, insert-mode |
| `/stop` | cancel every non-terminal task | client loop over existing `cancel` |
| `/help` | open this command menu | UI only |

Session commands (`compact`, `undo`, `fork`, `handoff`, …) have no room referent and are excluded. The room menu is its own table, not a reuse of `COMMAND_POLICY` ([palette.ts:18](../../../desktop/src/state/palette.ts)).

**Slash never hijacks a message.** A draft only executes as a command when it resolves to a row in this table (`/stop`, `/reload`, `/roles`, `/reset <role>`). `/help` posts nothing — it just opens the menu. Any other `/…` text posts as an ordinary room message, so the room stays a chat surface where a leading slash is legal text.

### 6.4 Model / thinking

No composer control. The send preview names each target role with its model (`@research · opus`, `@writer · glm`), read-only. Changing a model stays in the role editor.

`role_view` gains `supportsVision` so the preview can name a role whose model cannot take the attached images before the post is refused. It is used for preview text only, never to disable the send control.

### 6.5 Send preview

Next to the send control:

- `k 个任务` when `k` mentions resolve to roles, else `不派活`.
- `n 张图` when images are attached.
- `@research 正在忙，这条会排队` for any target role that is currently running — the queue is real ([manager.py:606](../../../zenagent/app/swarm/manager.py)) and the user should see it before sending.
- `@writer 不支持图片` for any target whose `supportsVision` is false while images are attached.

The preview only claims what it can know: a role's own run state. The global concurrency semaphore (`max_concurrent=3`) is not on the wire, so the preview does not promise anything about it.

Newline/send key handling is unchanged (`isSubmitKey`).

### 6.6 What does not change

Plate geometry, resize, auto-grow, token boxes, menu keyboard behavior, approval/ask cards, the role mention menu, and per-task cancel/retry in the task list.

## 7. Wire and storage changes

| Change | Location | Optional | Notes |
|---|---|---|---|
| `RoomMessage.ref`, `.images` | [room.py:40](../../../zenagent/app/swarm/room.py) | yes | old JSONL lines load with defaults |
| emit `ref` / `images` | [room.py:270](../../../zenagent/app/swarm/room.py) | only when non-empty | keeps records small |
| `post` accepts `ref`, `images` | [ops_swarm.py:65](../../../zenagent/cli/rpc/ops_swarm.py) | yes | validate ref + image capability |
| quote + attachment names in prompt | [protocol.py:22](../../../zenagent/app/swarm/protocol.py) | — | pure rendering |
| image blocks to `Agent.prompt` | [runner.py:55](../../../zenagent/app/swarm/runner.py) | — | load per task |
| `SwarmMessage` type, `swarmStore.post(text, opts)` | [swarmStore.ts:188](../../../desktop/src/state/swarmStore.ts) | — | |
| `roomQuote`, `roomImages` | composer store | — | per project, separate from coding's `images` |
| `supportsVision` on the role view | [ops_swarm.py](../../../zenagent/cli/rpc/ops_swarm.py) `role_view` | — | derived from the same model check |

## 8. Interaction details

- The quote strip sits inside `ComposerRoot` above the plate, so it travels with the composer and clears on send.
- Quote does not imply a target; `@` decides dispatch. A quoted message with no `@` posts a note that references the quote — nobody runs.
- Busy targets are stated in the preview, not blocked: the message queues exactly as it does today.

## 9. Tests

**Python**

- post with `ref` snapshots author and text; unknown ref refused; system notice not quotable.
- image post fans out one task per mention, each carrying the same image blocks.
- a role whose model cannot take images refuses the whole post, naming the role.
- old room-log lines without `ref`/`images` still load.
- `injected_prompt` snapshot with and without a quote.

**Desktop unit**

- quote strip lifecycle (set, clear on send, clear on Escape with empty draft).
- quote and images do not survive a project switch.
- room command table filters session commands out; a literal `/…` that is not a room command posts as text.
- preview counts: tasks, images, busy roles, non-vision roles.
- send disabled for images-without-mention.

**E2E**

- quote a message → send → the outgoing `swarm post` payload carries `ref` (the bridge mock cannot render a task prompt; prompt rendering is covered by the Python snapshot test).
- attach an image → chip renders → fan-out hint shows.
- `/stop` cancels queued and running tasks.
- `@busy-role` preview says the message will queue.

**Gates:** tsc, node test runner, style-token lint, architecture budget (watch `RoomComposer` growth).

## 10. Left to the builder

- Whether `/stop` should become one server-side atomic op instead of a client loop.
- Whether quotes stay single-per-message (current) or become multi.
- How much of the quoted text the card shows (two lines vs. full).
- Where the shared image-path reader lives once extracted from `server.py`.

## 11. Sequence

1. Room schema + wire (`ref`, `images`) with Python tests.
2. Prompt injection (quote block, attachment names).
3. Runner: image blocks per task.
4. Composer adapter: quote strip, attachments, preview, command table.
5. Feed: quote card and jump-to.
6. E2E.
