# Swarm Composer Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the swarm room composer the controls coding/zen already have — quoting a message as context, image attachments fanned out to the tasks a message spawns, and a room `/` command table — while the composer stays bound to the room rather than any task.

**Architecture:** The room message gains an optional `ref` snapshot and an optional `images` list; a task's prompt is built from that message, so both travel to every task the message spawns with no new RPC op. The desktop keeps draft, quote and images per project in the composer store, and the room adapter renders a quote strip, attachments, a send preview and a five-line command table on the shared plate.

**Tech Stack:** Python 3.11+ (pytest, asyncio), Tauri + React 18 + Zustand, `node --test` for desktop units, Playwright for e2e.

**Spec:** `docs/superpowers/specs/2026-11-12-swarm-composer-parity-design.md`

## Global Constraints

- Every new wire/record field is optional; a room log written before this change must load unchanged.
- The composer belongs to the room: no control may silently act on an arbitrary task.
- No new RPC ops — `swarm post` gains two optional fields and nothing else.
- `@` decides dispatch; a quote supplies context only.
- Room commands never hijack a message: only an exact room command executes; any other `/…` posts as text.
- No paste, no thumbnails, no Tauri asset protocol.
- Vue/React styling uses `--dsw-*` / `--ds-*` tokens only; `pnpm lint:tokens` must stay clean.
- Architecture budget (`python3 scripts/check_architecture.py`) and `ruff` stay green.

**Deviations from the spec (decided while planning):**

- `/roles` is dropped. The roster is permanently visible in the swarm sidebar, so the command would add nothing. The table is `/reload`, `/reset <role>`, `/stop`, `/help`.
- The attach e2e is replaced by unit coverage: the e2e harness has no file-dialog stub, and adding one is native work outside this feature.
- `supportsVision` is added in `SwarmManager.roles()` rather than in `roster.role_view()`. Vision is a property of the built agent's model, and the library view (`roles action=library`) has no built agent; keeping it in `roles()` leaves the library payload untouched.

## Review Focus

1. A pre-change `room.jsonl` line (no `ref`, no `images`) still loads and renders — task 1.
2. A quote id that does not exist in this room (stale, or taken in another project) is refused, never silently dropped — tasks 4, 5, 7.
3. A post with images where any mentioned role's model lacks vision creates **no** tasks at all — task 4.
4. An unreadable image path is refused before any task starts — task 4.
5. A message beginning with `/` that is not a room command still posts as text — task 9.

---

### Task 1: Room message `ref` and `images`

**Files:**
- Modify: `zenagent/app/swarm/room.py:20-48`, `:182-196`, `:270-281`
- Test: `tests/test_swarm_room.py`

**Interfaces:**
- Produces: `RoomRef(id: str, author_kind: str, author_id: str, text: str)`; `RoomMessage.ref: RoomRef | None = None`; `RoomMessage.images: list[str] = field(default_factory=list)`; record keys `ref` (`{id, author:{kind,id}, text}`) and `images`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_swarm_room.py`:

```python
def test_message_record_carries_a_ref_snapshot_and_images(tmp_path):
    store = SwarmStore(tmp_path)
    ref = RoomRef(id="m0", author_kind="agent", author_id="research", text="结论是 Z")
    message = _message(ref=ref, images=["/tmp/a.png"])
    store.append_message(message)

    record = _message_record(message)
    assert record["ref"] == {
        "id": "m0",
        "author": {"kind": "agent", "id": "research"},
        "text": "结论是 Z",
    }
    assert record["images"] == ["/tmp/a.png"]


def test_replayed_message_restores_ref_and_images(tmp_path):
    store = SwarmStore(tmp_path)
    store.append_message(
        _message(
            id="m1",
            ref=RoomRef(id="m0", author_kind="agent", author_id="research", text="Z"),
            images=["/tmp/a.png", "/tmp/b.png"],
        )
    )
    reloaded = SwarmStore(tmp_path)
    reloaded.load()

    message = reloaded.messages[0]
    assert message.ref is not None
    assert (message.ref.id, message.ref.author_id, message.ref.text) == ("m0", "research", "Z")
    assert message.images == ["/tmp/a.png", "/tmp/b.png"]


def test_a_pre_change_record_still_loads(tmp_path):
    (tmp_path / "room.jsonl").write_text(
        json.dumps({"id": "m_old", "ts": 1, "author": {"kind": "user", "id": "you"}, "text": "hi"})
        + "\n",
        encoding="utf-8",
    )
    store = SwarmStore(tmp_path)
    store.load()

    assert store.messages[0].ref is None
    assert store.messages[0].images == []
    assert store.warnings == []


def test_a_malformed_ref_is_ignored_without_dropping_the_message(tmp_path):
    (tmp_path / "room.jsonl").write_text(
        json.dumps(
            {
                "id": "m1",
                "ts": 1,
                "author": {"kind": "user", "id": "you"},
                "text": "hi",
                "ref": "not-an-object",
                "images": "nope",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = SwarmStore(tmp_path)
    store.load()

    assert store.messages[0].text == "hi"
    assert store.messages[0].ref is None
    assert store.messages[0].images == []
```

Update the import at the top of the file to include `RoomRef`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_swarm_room.py -q`
Expected: FAIL — `ImportError: cannot import name 'RoomRef'`.

- [ ] **Step 3: Implement the fields and the (de)serializer**

In `zenagent/app/swarm/room.py`:

```python
@dataclass
class RoomRef:
    """A snapshot of the message this one quotes: the task prompt must be
    self-contained, and the quote card must render when the source is outside
    the loaded window."""

    id: str
    author_kind: str
    author_id: str
    text: str


@dataclass
class RoomMessage:
    id: str
    ts: int
    author_kind: str
    author_id: str
    text: str
    mentions: list[str] = field(default_factory=list)
    task_id: str | None = None
    chain_depth: int = 0
    ref: RoomRef | None = None
    images: list[str] = field(default_factory=list)
```

`_message_record` builds the dict as today, then adds:

```python
    if message.ref is not None:
        record["ref"] = {
            "id": message.ref.id,
            "author": {"kind": message.ref.author_kind, "id": message.ref.author_id},
            "text": message.ref.text,
        }
    if message.images:
        record["images"] = list(message.images)
    return record
```

`_apply_message` parses defensively through a module helper:

```python
def _parse_ref(raw) -> RoomRef | None:
    if not isinstance(raw, dict):
        return None
    author = raw.get("author")
    if not isinstance(author, dict):
        author = {}
    ref_id = raw.get("id")
    if not isinstance(ref_id, str) or not ref_id:
        return None
    return RoomRef(
        id=ref_id,
        author_kind=str(author.get("kind") or "system"),
        author_id=str(author.get("id") or ""),
        text=str(raw.get("text") or ""),
    )
```

and in `_apply_message`:

```python
            ref=_parse_ref(raw.get("ref")),
            images=[str(item) for item in raw.get("images") or [] if isinstance(item, str)],
```

Add `"RoomRef"` to `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_swarm_room.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/room.py tests/test_swarm_room.py
git commit -m "feat(swarm): carry a quote snapshot and images on room messages"
```

---

### Task 2: Render the quote and attachments in the task prompt

**Files:**
- Modify: `zenagent/app/swarm/protocol.py:22-28`
- Test: `tests/test_swarm_protocol.py` (create)

**Interfaces:**
- Consumes: `RoomMessage.ref`, `RoomMessage.images` from task 1.
- Produces: `injected_prompt(message, task)` output unchanged when `ref` is `None` and `images` is empty.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_swarm_protocol.py`:

```python
from zenagent.app.swarm.protocol import injected_prompt
from zenagent.app.swarm.room import RoomMessage, RoomRef, TaskRecord


def _task() -> TaskRecord:
    return TaskRecord(
        id="t1", assignee="research", state="running", chain_id="c1", chain_depth=1
    )


def _message(**overrides) -> RoomMessage:
    fields = {
        "id": "m1",
        "ts": 1,
        "author_kind": "user",
        "author_id": "you",
        "text": "@research 复核一下",
    }
    fields.update(overrides)
    return RoomMessage(**fields)


def test_prompt_is_unchanged_without_a_ref_or_images():
    prompt = injected_prompt(_message(), _task())

    assert prompt.endswith("Message:\n@research 复核一下")
    assert "Quoted" not in prompt
    assert "Attached images" not in prompt


def test_prompt_renders_the_quote_before_the_message():
    prompt = injected_prompt(
        _message(ref=RoomRef(id="m0", author_kind="agent", author_id="research", text="结论是 Z\n第二行")),
        _task(),
    )

    assert "Quoted agent:research (m0):\n> 结论是 Z\n> 第二行\n\nMessage:\n@research 复核一下" in prompt


def test_prompt_lists_attachment_names_not_absolute_paths():
    prompt = injected_prompt(_message(images=["/tmp/a/b.png"]), _task())

    assert "Attached images:\n- b.png\n" in prompt
    assert "/tmp/a/b.png" not in prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_swarm_protocol.py -q`
Expected: FAIL — the quote and attachment assertions miss.

- [ ] **Step 3: Implement the two blocks**

In `injected_prompt`, keep the existing head string byte-identical and insert an `extra` block between it and `Message:`:

```python
def injected_prompt(message: RoomMessage, task: TaskRecord) -> str:
    head = (
        f"[swarm] from {message.author_kind}:{message.author_id}\n"
        f"task: {task.id} · chain: {task.chain_id} · depth: {task.chain_depth}\n\n"
        f"{PROTOCOL_TEXT}\n\n"
    )
    extra = ""
    if message.ref is not None:
        extra += f"Quoted {message.ref.author_kind}:{message.ref.author_id} ({message.ref.id}):\n"
        for line in message.ref.text.splitlines():
            extra += f"> {line}\n"
        extra += "\n"
    if message.images:
        extra += "Attached images:\n"
        for path in message.images:
            extra += f"- {Path(path).name}\n"
        extra += "\n"
    return f"{head}{extra}Message:\n{message.text}"
```

Add `from pathlib import Path` at the top.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_swarm_protocol.py tests/test_swarm_runner.py -q`
Expected: PASS (the runner tests prove the no-ref prompt path stayed identical).

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/protocol.py tests/test_swarm_protocol.py
git commit -m "feat(swarm): render quoted context and attachment names in task prompts"
```

---

### Task 3: Pass image blocks to the role's agent

**Files:**
- Modify: `zenagent/app/swarm/runner.py:1-23`, `:67-69`
- Test: `tests/test_swarm_runner.py`

**Interfaces:**
- Consumes: `RoomMessage.images`.
- Produces: `read_message_images(message: RoomMessage) -> list | None` in `runner.py` (module level, monkeypatchable).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_swarm_runner.py`:

```python
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def test_images_reach_the_agent_as_content_blocks(tmp_path, runtime_factory):
    image_path = tmp_path / "a.png"
    image_path.write_bytes(PNG_1PX)
    runtime = await runtime_factory(stream_fn=text_stream("看图完毕"))
    store, _router, current, post, admit = _bind(tmp_path)
    task = _task(store)
    current["task"] = task
    message = _message(store, text="@research 看图")
    message.images.append(str(image_path))

    record = await _runner(runtime, store, post, admit).run(task, message)

    assert record.state == "done"
    first = runtime.agent.messages[0]
    assert isinstance(first, UserMessage)
    assert any(isinstance(block, ImageContent) for block in first.content)
    assert any(isinstance(block, TextContent) and "看图" in block.text for block in first.content)
```

Add `import base64` and import `ImageContent`, `TextContent`, `UserMessage` from `zenagent.types` in that file.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_swarm_runner.py::test_images_reach_the_agent_as_content_blocks -q`
Expected: FAIL — no image block is present.

- [ ] **Step 3: Implement the reader and use it**

In `zenagent/app/swarm/runner.py`:

```python
from pathlib import Path

from zenagent.attachments import read_image_argument


def read_message_images(message: RoomMessage) -> list | None:
    """Load a message's attachment paths once per task; ``None`` keeps the
    text-only prompt path byte-identical to before images existed."""
    if not message.images:
        return None
    return [read_image_argument(Path(path)) for path in message.images]
```

and in `run()`:

```python
        async def drive() -> None:
            await self.agent.prompt(
                injected_prompt(message, running),
                images=read_message_images(message),
            )
            await self.agent.wait_for_idle()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_swarm_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/runner.py tests/test_swarm_runner.py
git commit -m "feat(swarm): deliver room attachments to each delegated role"
```

---

### Task 4: Validate `post` and expose `supportsVision`

**Files:**
- Modify: `zenagent/app/swarm/manager.py:198-202`, `:256-269`
- Test: `tests/test_swarm_manager.py`

**Interfaces:**
- Produces: `SwarmManager.post(text: str, *, ref: str | None = None, images: list[str] | None = None) -> list[TaskRecord]`, raising `ValueError` for an unknown/system ref, a non-vision target, or an unreadable image; `SwarmManager.roles()` entries gain `"supportsVision": bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_swarm_manager.py` (uses the existing `_manager`/`_write_role` helpers; `_write_role` roles build a real runtime):

```python
async def test_unknown_ref_is_refused():
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        with pytest.raises(ValueError, match="unknown ref"):
            await manager.post("@research hi", ref="m_missing")
    finally:
        await manager.close()


async def test_system_notice_is_not_quotable():
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        notice = manager.store.messages[0]
        assert notice.author_kind == "system"
        with pytest.raises(ValueError, match="cannot quote a system message"):
            await manager.post("@research hi", ref=notice.id)
    finally:
        await manager.close()


async def test_post_snapshots_the_quoted_message_for_every_task():
    manager = _manager()
    _write_role("research")
    _write_role("coder")
    await manager.start()
    try:
        first = await manager.post("@research 出结论")
        source = manager.store.messages[-1]
        tasks = await manager.post("@research @coder 复核", ref=source.id)

        assert len(tasks) == 2
        message = manager.store.messages[-1]
        assert message.ref is not None
        assert (message.ref.id, message.ref.text) == (source.id, source.text)
        assert message.ref.author_kind == source.author_kind
    finally:
        await manager.close()


async def test_images_are_refused_before_any_task_is_created(tmp_path):
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        with pytest.raises(ValueError, match="cannot read image"):
            await manager.post("@research 看图", images=[str(tmp_path / "missing.png")])
        assert manager.tasks() == []
    finally:
        await manager.close()


async def test_a_non_vision_target_refuses_the_whole_post(tmp_path):
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        agent = manager.agents["research"]
        agent.agent.model = replace(agent.agent.model, supports_vision=False)
        image = tmp_path / "a.png"
        image.write_bytes(PNG_1PX)

        with pytest.raises(ValueError, match="@research cannot accept images"):
            await manager.post("@research 看图", images=[str(image)])
        assert manager.tasks() == []
    finally:
        await manager.close()


async def test_roles_report_vision_support():
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        assert all("supportsVision" in role for role in manager.roles())
    finally:
        await manager.close()
```

The file needs `import base64`, `from dataclasses import replace`, and a module-level `PNG_1PX` constant (copy the one from task 3). Import `user_message`-free — this test never runs a task.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_swarm_manager.py -q -k "ref or images or vision"`
Expected: FAIL — `post() got an unexpected keyword argument 'ref'`.

- [ ] **Step 3: Implement validation and the role flag**

In `zenagent/app/swarm/manager.py`, import `RoomRef` and `read_image_argument`, then:

```python
    async def post(
        self,
        text: str,
        *,
        ref: str | None = None,
        images: list[str] | None = None,
    ) -> list[TaskRecord]:
        mentions = parse_mentions(text)
        quote = self._quote_snapshot(ref)
        paths = self._image_paths(images or [], mentions)
        message = RoomMessage(
            ...,
            mentions=mentions,
            ref=quote,
            images=paths,
        )
```

Helpers on the class:

```python
    def _quote_snapshot(self, ref: str | None) -> RoomRef | None:
        if ref is None:
            return None
        source = next((item for item in self.store.messages if item.id == ref), None)
        if source is None:
            raise ValueError(f"unknown ref {ref}")
        if source.author_kind == "system":
            raise ValueError(f"cannot quote a system message {ref}")
        return RoomRef(
            id=source.id,
            author_kind=source.author_kind,
            author_id=source.author_id,
            text=source.text,
        )

    def _image_paths(self, images: list[str], mentions: list[str]) -> list[str]:
        if not images:
            return []
        for role_id in mentions:
            agent = self.agents.get(role_id)
            if agent is not None and not agent.agent.model.supports_vision:
                raise ValueError(f"@{role_id} cannot accept images")
        for path in images:
            read_image_argument(Path(path))
        return list(images)
```

`self.agents` exists only after `start()`; `post` is only reachable through a started manager.

In `roles()`:

```python
    def roles(self) -> list[dict]:
        views = []
        for agent in self.agents.values():
            view = role_view(agent.pending_apply or agent.role)
            view["supportsVision"] = agent.agent.model.supports_vision
            views.append(view)
        return views
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_swarm_manager.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/app/swarm/manager.py tests/test_swarm_manager.py
git commit -m "feat(swarm): validate quotes and attachments at post time"
```

---

### Task 5: Accept `ref` and `images` on the wire

**Files:**
- Modify: `zenagent/cli/rpc/ops_swarm.py:64-70`
- Test: `tests/test_rpc_swarm.py`

**Interfaces:**
- Consumes: `manager.post(text, ref=..., images=...)` from task 4.
- Produces: `post` accepts `ref: str` and `images: list[str]`; `ValueError` from the manager becomes a `Refusal` (an `error` frame with code `swarm_invalid`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_rpc_swarm.py`, update `FakeSwarm.post` to the new signature and record the options:

```python
    async def post(self, text, *, ref=None, images=None):
        self.posts.append((text, ref, tuple(images or ())))
        return [...]
```

Then add:

```python
async def test_post_passes_ref_and_images(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

    await _call(
        host,
        {
            "type": "swarm",
            "op": "post",
            "id": "s1",
            "text": "@research hi",
            "ref": "m_0",
            "images": ["/tmp/a.png"],
        },
    )

    assert fake.posts == [("@research hi", "m_0", ("/tmp/a.png",))]
    assert writes[-1]["ok"] is True


async def test_post_rejects_a_bad_ref_type(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

    await _call(host, {"type": "swarm", "op": "post", "id": "s1", "text": "hi", "ref": 3})

    assert writes[-1]["type"] == "error"
    assert fake.posts == []
```

Fix any existing post assertion that compared `fake.posts == ["@research hi"]` to the tuple shape.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_rpc_swarm.py -q -k post`
Expected: FAIL — the fake receives only `text`.

- [ ] **Step 3: Implement the handler**

In the `elif op == "post":` branch:

```python
            elif op == "post":
                text = request.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise Refusal("post needs text")
                ref = request.get("ref")
                if ref is not None and not isinstance(ref, str):
                    raise Refusal("post ref must be a message id")
                images = request.get("images")
                if images is not None and (
                    not isinstance(images, list)
                    or not all(isinstance(item, str) for item in images)
                ):
                    raise Refusal("post images must be a list of file paths")
                try:
                    tasks = await manager.post(text, ref=ref, images=images)
                except ValueError as exc:
                    raise Refusal(str(exc)) from exc
                payload = {"tasks": [manager.task_view(task) for task in tasks]}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_rpc_swarm.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zenagent/cli/rpc/ops_swarm.py tests/test_rpc_swarm.py
git commit -m "feat(rpc): accept a quoted ref and images on swarm post"
```

---

### Task 6: Desktop wire types, store options, dev mock

**Files:**
- Modify: `desktop/src/rpc/types-swarm.ts:9-42`, `desktop/src/state/swarmStore.ts:89`, `:188-190`, `desktop/src/dev/swarmMock.ts:39-76`, `:208-240`, `:452-490`
- Test: `desktop/tests/swarmStore.test.ts`

**Interfaces:**
- Produces: `SwarmRef { id: string; author: SwarmAuthor; text: string }`; `SwarmMessage.ref?: SwarmRef | null`, `SwarmMessage.images?: string[]`; `SwarmRole.supportsVision?: boolean`; `post(text: string, options?: { ref?: string; images?: string[] })`.

- [ ] **Step 1: Write the failing test**

Append to `desktop/tests/swarmStore.test.ts` (the file already installs a capture sender; follow its existing pattern):

```ts
test('post forwards the ref and images only when given', async () => {
  const calls: Record<string, unknown>[] = []
  _useSwarmSender({
    send: async (request) => {
      calls.push(request)
      return { type: 'result', id: 'r1', ok: true, tasks: [] }
    },
  })

  await useSwarmStore.getState().post('@research hi')
  await useSwarmStore.getState().post('@research hi', { ref: 'm1', images: ['/tmp/a.png'] })

  assert.deepEqual(calls[0], { type: 'swarm', op: 'post', text: '@research hi' })
  assert.deepEqual(calls[1], {
    type: 'swarm',
    op: 'post',
    text: '@research hi',
    ref: 'm1',
    images: ['/tmp/a.png'],
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm --dir desktop test`
Expected: FAIL — `post` takes one argument.

- [ ] **Step 3: Implement types, store and mock**

`types-swarm.ts`:

```ts
export interface SwarmRef {
  id: string
  author: SwarmAuthor
  text: string
}
```

Add to `SwarmMessage`: `ref?: SwarmRef | null` and `images?: string[]`.
Add to `SwarmRole`: `supportsVision?: boolean`.

`swarmStore.ts`:

```ts
  post(text: string, options?: { ref?: string; images?: string[] }): Promise<void>
  ...
  async post(text, options = {}) {
    await send({
      type: 'swarm',
      op: 'post',
      text,
      ...(options.ref !== undefined ? { ref: options.ref } : {}),
      ...(options.images !== undefined && options.images.length > 0
        ? { images: options.images }
        : {}),
    })
  },
```

`dev/swarmMock.ts`: give `RESEARCH` `supportsVision: true` and `BUILDER` `supportsVision: false`; copy the flag in `roleView`. In `handleSwarm`'s `post`, pass the payload through:

```ts
  if (op === 'post') {
    const text = String(payload.text ?? '')
    const refId = typeof payload.ref === 'string' ? payload.ref : undefined
    const source = refId === undefined ? undefined : rows.swarmMessages.find((m) => m.id === refId)
    const images = Array.isArray(payload.images) ? payload.images.map(String) : []
    const message = appendMessage(rows, {
      author: { kind: 'user', id: 'you' },
      text,
      ref: source === undefined ? null : { id: source.id, author: source.author, text: source.text },
      images,
    })
```

and extend `appendMessage`'s `fields` with `ref?: SwarmRef | null; images?: string[]`, writing them onto the message.

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm --dir desktop test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/rpc/types-swarm.ts desktop/src/state/swarmStore.ts desktop/src/dev/swarmMock.ts desktop/tests/swarmStore.test.ts
git commit -m "feat(desktop): carry quote and images through the swarm post"
```

---

### Task 7: Per-project room composer state

**Files:**
- Modify: `desktop/src/state/stores.ts:470-505`, `desktop/src/ui/composer/RoomComposer.tsx:25-26`, `:68-78`
- Test: `desktop/tests/roomComposerStore.test.ts` (create)

**Interfaces:**
- Produces: `room: Record<string, RoomComposerState>` where `RoomComposerState = { draft: string; quote: SwarmRef | null; images: AttachmentImage[] }`; setters `setRoomDraft(project, draft)`, `setRoomQuote(project, quote)`, `addRoomImages(project, paths)`, `removeRoomImage(project, id)`, `clearRoomImages(project)`; `roomComposerState(project)` reader returning the stored state or a frozen empty one.

- [ ] **Step 1: Write the failing test**

Create `desktop/tests/roomComposerStore.test.ts`:

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { roomComposerState, useComposerStore } from '../src/state/stores.ts'

test('room drafts, quotes and images never leak across projects', () => {
  const store = useComposerStore.getState()
  store.setRoomDraft('/a', '@research hi')
  store.setRoomQuote('/a', { id: 'm1', author: { kind: 'user', id: 'you' }, text: 'q' })
  store.addRoomImages('/a', ['/tmp/a.png'])

  assert.equal(roomComposerState('/a').draft, '@research hi')
  assert.equal(roomComposerState('/b').draft, '')
  assert.equal(roomComposerState('/b').quote, null)
  assert.deepEqual(roomComposerState('/b').images, [])
})

test('images are added and removed per project', () => {
  const store = useComposerStore.getState()
  store.addRoomImages('/a', ['/tmp/a.png', '/tmp/b.png'])
  const [first] = roomComposerState('/a').images

  store.removeRoomImage('/a', first.id)

  assert.deepEqual(
    roomComposerState('/a').images.map((image) => image.name),
    ['b.png'],
  )
  store.clearRoomImages('/a')
  assert.deepEqual(roomComposerState('/a').images, [])
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm --dir desktop test`
Expected: FAIL — `roomComposerState` is not exported.

- [ ] **Step 3: Implement the keyed store**

In `desktop/src/state/stores.ts`, import `SwarmRef` from `../rpc/types` and replace `roomDraft`/`setRoomDraft` with:

```ts
export interface RoomComposerState {
  draft: string
  quote: SwarmRef | null
  images: AttachmentImage[]
}

const EMPTY_ROOM: RoomComposerState = { draft: '', quote: null, images: [] }

/** The room's draft, quote and images by project: a quote taken in room A must
 * never ride into room B, where its id would be refused. */
export function roomComposerState(project: string): RoomComposerState {
  return useComposerStore.getState().room[project] ?? EMPTY_ROOM
}
```

State interface and implementation:

```ts
  room: Record<string, RoomComposerState>
  setRoomDraft(project: string, draft: string): void
  setRoomQuote(project: string, quote: SwarmRef | null): void
  addRoomImages(project: string, paths: string[]): void
  removeRoomImage(project: string, id: string): void
  clearRoomImages(project: string): void
```

```ts
const patchRoom = (
  state: { room: Record<string, RoomComposerState> },
  project: string,
  next: Partial<RoomComposerState>,
) => ({
  room: {
    ...state.room,
    [project]: { ...(state.room[project] ?? EMPTY_ROOM), ...next },
  },
})
```

with `room: {}` in the initial state, and:

```ts
  addRoomImages: (project, paths) =>
    set((state) => {
      const current = state.room[project] ?? EMPTY_ROOM
      return patchRoom(state, project, {
        images: [
          ...current.images,
          ...paths.map((path) => ({
            id: `img${(imageSeq += 1)}`,
            path,
            name: path.split('/').pop() ?? path,
          })),
        ],
      })
    }),
  removeRoomImage: (project, id) =>
    set((state) =>
      patchRoom(state, project, {
        images: (state.room[project] ?? EMPTY_ROOM).images.filter((image) => image.id !== id),
      }),
    ),
  clearRoomImages: (project) => set((state) => patchRoom(state, project, { images: [] })),
```

Update `RoomComposer` to read `const project = useSwarmStore((s) => s.loadedProject) ?? ''` and use the keyed selectors:

```ts
  const draft = useComposerStore((s) => s.room[project]?.draft ?? '')
  const images = useComposerStore((s) => s.room[project]?.images ?? NO_IMAGES)
```

with `const NO_IMAGES: AttachmentImage[] = []` at module scope. Replace `setDraft(value)` calls with `setRoomDraft(project, value)`.

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm --dir desktop test && pnpm --dir desktop exec tsc --noEmit`
Expected: PASS and no type errors.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/stores.ts desktop/src/ui/composer/RoomComposer.tsx desktop/tests/roomComposerStore.test.ts
git commit -m "feat(desktop): key the room draft, quote and images by project"
```

---

### Task 8: Quote strip, attachments and send preview

**Files:**
- Create: `desktop/src/state/roomPreview.ts`, `desktop/tests/roomPreview.test.ts`, `desktop/src/ui/composer/useImageDrop.ts`, `desktop/src/ui/composer/RoomComposer.module.css`
- Modify: `desktop/src/ui/composer/RoomComposer.tsx`, `desktop/src/ui/composer/InputBar.tsx:140-150`, `desktop/src/i18n/messages-swarm.ts`

**Interfaces:**
- Consumes: `roomComposerState` setters (task 7), `post(text, {ref, images})` (task 6).
- Produces: `roleMentions(text, roleIds): string[]`; `roomSendPreview(mentions, roles, agents, imageCount): { tasks: number; images: number; queued: string[]; noVision: string[] }`; `useImageDrop(add: (paths: string[]) => void): void`.

- [ ] **Step 1: Write the failing tests**

Create `desktop/tests/roomPreview.test.ts`:

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { roleMentions, roomSendPreview } from '../src/state/roomPreview.ts'
import type { SwarmAgentState, SwarmRole } from '../src/rpc/types.ts'

const roles = [
  { id: 'research', name: 'Research', supportsVision: true },
  { id: 'writer', name: 'Writer', supportsVision: false },
] as unknown as SwarmRole[]

const agents = [
  { agent: 'research', name: 'Research', state: 'running', task: 't1', pendingApply: false },
] as unknown as SwarmAgentState[]

test('mentions resolve only to known roles', () => {
  assert.deepEqual(roleMentions('@research 和 @ghost 看 @writer', ['research', 'writer']), [
    'research',
    'writer',
  ])
  assert.deepEqual(roleMentions('@research@writer', ['research', 'writer']), [])
})

test('the preview counts tasks and names busy and non-vision targets', () => {
  const preview = roomSendPreview(['research', 'writer'], roles, agents, 1)
  assert.equal(preview.tasks, 2)
  assert.equal(preview.images, 1)
  assert.deepEqual(preview.queued, ['research'])
  assert.deepEqual(preview.noVision, ['writer'])
})

test('no mention means no task and no image send', () => {
  const preview = roomSendPreview([], roles, agents, 2)
  assert.equal(preview.tasks, 0)
  assert.deepEqual(preview.queued, [])
  assert.deepEqual(preview.noVision, [])
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm --dir desktop test`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement the preview, the drop hook and the composer UI**

`roomPreview.ts`:

```ts
const MENTION_RE = /(?<![\w@])@([a-z0-9][a-z0-9._-]*)/g

export function roleMentions(text: string, roleIds: readonly string[]): string[] {
  const known = new Set(roleIds)
  const found: string[] = []
  for (const match of text.matchAll(MENTION_RE)) {
    const id = match[1]
    if (known.has(id) && !found.includes(id)) found.push(id)
  }
  return found
}

export interface RoomPreview {
  tasks: number
  images: number
  queued: string[]
  noVision: string[]
}

export function roomSendPreview(
  mentions: readonly string[],
  roles: readonly SwarmRole[],
  agents: readonly SwarmAgentState[],
  imageCount: number,
): RoomPreview {
  const running = new Set(agents.filter((a) => a.state === 'running').map((a) => a.agent))
  const byId = new Map(roles.map((role) => [role.id, role]))
  return {
    tasks: mentions.length,
    images: imageCount,
    queued: mentions.filter((id) => running.has(id)),
    noVision:
      imageCount === 0
        ? []
        : mentions.filter((id) => byId.get(id)?.supportsVision === false),
  }
}
```

`useImageDrop.ts`:

```ts
import { useEffect } from 'react'
import { attachDragDrop } from './dragDrop'

/** One drag-drop subscription for every composer adapter: images only. */
export function useImageDrop(add: (paths: string[]) => void): void {
  useEffect(
    () =>
      attachDragDrop((paths) => {
        const images = paths.filter((path) => /\.(png|jpe?g|gif|webp|bmp)$/i.test(path))
        if (images.length > 0) add(images)
      }),
    [add],
  )
}
```

Replace `InputBar`'s inline `attachDragDrop` effect with `useImageDrop(addImages)` (delete the now-unused imports).

`RoomComposer.tsx`:

- `const agents = useSwarmStore((s) => s.agents)`, `const quote = useComposerStore((s) => s.room[project]?.quote ?? null)`, plus the task-7 selectors.
- `useImageDrop((paths) => addRoomImages(project, paths))`.
- Render above the plate, inside `ComposerRoot`:

```tsx
{quote !== null && (
  <div className={css.quote} data-swarm-quote-strip>
    <span className={css.quoteAuthor}>{quote.author.id}</span>
    <span className={css.quoteText}>{quote.text}</span>
    <button
      type="button"
      className={css.quoteClear}
      aria-label={t('swarm.quoteClear')}
      onClick={() => setRoomQuote(project, null)}
    >
      ×
    </button>
  </div>
)}
```

- `leading`: the attach button (`PlusIcon`, `pickImagePaths` → `addRoomImages`), labelled `t('composer.attach')`.
- `attachments`: `<ComposerAttachments items={images} onRemove={(id) => removeRoomImage(project, id)} />`.
- `trailing`: the preview. Compute it once in the component:

```tsx
  const mentions = roleMentions(draft, roles.map((role) => role.id))
  const preview = roomSendPreview(mentions, roles, agents, images.length)
  const targets = mentions
    .map((id) => {
      const role = roles.find((entry) => entry.id === id)
      return role === undefined ? `@${id}` : `@${id} · ${role.model ?? '—'}`
    })
    .join('  ')
```

and render:

```tsx
<span className={css.preview} data-swarm-preview>
  {targets !== '' && <span className={css.targets}>{targets}</span>}
  <span>{preview.tasks > 0 ? t('swarm.previewTasks', { n: preview.tasks }) : t('swarm.previewNoTasks')}</span>
  {preview.images > 0 && <span>{t('swarm.previewImages', { n: preview.images })}</span>}
  {preview.queued.map((role) => (
    <span key={`q-${role}`} className={css.warn}>{t('swarm.previewQueued', { role })}</span>
  ))}
  {preview.noVision.map((role) => (
    <span key={`v-${role}`} className={css.warn}>{t('swarm.previewNoVision', { role })}</span>
  ))}
  {images.length > 0 && preview.tasks === 0 && (
    <span className={css.warn}>{t('swarm.imagesNeedMention')}</span>
  )}
</span>
```

- `primary.disabled` becomes `draft.trim() === '' || busy || (images.length > 0 && preview.tasks === 0)`.
- Escape clears the quote when the draft is empty (after the menu's own Escape handling, which keeps priority while the menu is open):

```tsx
    if (event.key === 'Escape' && draft === '' && quote !== null) {
      event.preventDefault()
      setRoomQuote(project, null)
      return
    }
```
- `submit` passes options and clears all three:

```tsx
    const mentions = roleMentions(text, roles.map((role) => role.id))
    await post(text, {
      ...(quote !== null ? { ref: quote.id } : {}),
      ...(images.length > 0 ? { images: images.map((image) => image.path) } : {}),
    })
```

then `setRoomDraft(project, '')`, `setRoomQuote(project, null)`, `clearRoomImages(project)`.

`RoomComposer.module.css`: `.quote`, `.quoteAuthor`, `.quoteText`, `.quoteClear`, `.preview`, `.warn` — tokens only (e.g. `var(--dsw-alias-bg-layered)`, `var(--dsw-alias-label-secondary)`, `var(--dsw-alias-state-warning-primary)`, `var(--ds-radius-10)`); run the token lint to confirm names exist.

i18n: add to both dictionaries in `messages-swarm.ts`:
`swarm.quoteStrip`, `swarm.quoteClear`, `swarm.previewTasks` `'{n} 个任务'`, `swarm.previewNoTasks` `'不派活'`, `swarm.previewImages` `'{n} 张图'`, `swarm.previewQueued` `'@{role} 正在忙，这条会排队'`, `swarm.previewNoVision` `'@{role} 不支持图片'`, `swarm.imagesNeedMention` `'图片需要 @ 一个角色'` (English equivalents in `swarmEn`).

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm --dir desktop test && pnpm --dir desktop lint:tokens && pnpm --dir desktop exec tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/roomPreview.ts desktop/tests/roomPreview.test.ts desktop/src/ui/composer desktop/src/i18n/messages-swarm.ts
git commit -m "feat(desktop): quote strip, attachments and send preview in the room composer"
```

---

### Task 9: Room command table

**Files:**
- Create: `desktop/src/state/roomCommands.ts`, `desktop/tests/roomCommands.test.ts`
- Modify: `desktop/src/ui/composer/RoomComposer.tsx`, `desktop/src/i18n/messages-swarm.ts`

**Interfaces:**
- Produces: `parseRoomCommand(text: string, roleIds: readonly string[]): RoomCommand | null`; `roomCommandMenu(query: string): readonly RoomCommandEntry[]` with `RoomCommandEntry = { text: string; labelKey: string; insert: string }`.

- [ ] **Step 1: Write the failing test**

Create `desktop/tests/roomCommands.test.ts`:

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseRoomCommand, roomCommandMenu } from '../src/state/roomCommands.ts'

test('exact room commands parse', () => {
  assert.deepEqual(parseRoomCommand('/reload', ['research']), { kind: 'reload' })
  assert.deepEqual(parseRoomCommand('/stop', ['research']), { kind: 'stop' })
  assert.deepEqual(parseRoomCommand('/help', ['research']), { kind: 'help' })
  assert.deepEqual(parseRoomCommand('/reset research', ['research']), {
    kind: 'reset',
    roleId: 'research',
  })
})

test('an unknown reset target is reported, not posted', () => {
  assert.deepEqual(parseRoomCommand('/reset ghost', ['research']), {
    kind: 'unknown-reset',
    roleId: 'ghost',
  })
})

test('any other slash text is an ordinary message', () => {
  assert.equal(parseRoomCommand('/usr/bin 看看', ['research']), null)
  assert.equal(parseRoomCommand('/research hi', ['research']), null)
  assert.equal(parseRoomCommand('/stop now', ['research']), null)
})

test('the menu filters by prefix and insert mode appends a space', () => {
  assert.deepEqual(
    roomCommandMenu('/re').map((entry) => entry.insert),
    ['/reload ', '/reset '],
  )
  assert.deepEqual(roomCommandMenu('/sto').map((entry) => entry.insert), ['/stop'])
  assert.deepEqual(roomCommandMenu('hello'), [])
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm --dir desktop test`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement the table and wire it in**

`desktop/src/state/roomCommands.ts`:

```ts
export type RoomCommand =
  | { kind: 'reload' }
  | { kind: 'stop' }
  | { kind: 'help' }
  | { kind: 'reset'; roleId: string }
  | { kind: 'unknown-reset'; roleId: string }

export interface RoomCommandEntry {
  text: string
  labelKey: string
  insert: string
}

export const ROOM_COMMANDS: readonly RoomCommandEntry[] = [
  { text: '/reload', labelKey: 'swarm.commandReload', insert: '/reload ' },
  { text: '/reset', labelKey: 'swarm.commandReset', insert: '/reset ' },
  { text: '/stop', labelKey: 'swarm.commandStop', insert: '/stop ' },
  { text: '/help', labelKey: 'swarm.commandHelp', insert: '/help ' },
]

export function roomCommandMenu(query: string): readonly RoomCommandEntry[] {
  if (!query.startsWith('/')) return []
  return ROOM_COMMANDS.filter((entry) => entry.text.startsWith(query.trim()))
}

export function parseRoomCommand(
  text: string,
  roleIds: readonly string[],
): RoomCommand | null {
  const trimmed = text.trim()
  if (trimmed === '/reload') return { kind: 'reload' }
  if (trimmed === '/stop') return { kind: 'stop' }
  if (trimmed === '/help') return { kind: 'help' }
  const reset = /^\/reset\s+(\S+)$/.exec(trimmed)
  if (reset !== null) {
    const roleId = reset[1]
    return roleIds.includes(roleId)
      ? { kind: 'reset', roleId }
      : { kind: 'unknown-reset', roleId }
  }
  return null
}
```

`RoomComposer.tsx` — in `submit`, before posting:

```tsx
    const command = parseRoomCommand(text, roles.map((role) => role.id))
    if (command !== null) {
      setRoomDraft(project, '')
      await runRoomCommand(command)
      return
    }
```

with

```tsx
  const runRoomCommand = async (command: RoomCommand) => {
    if (command.kind === 'reload') return reload()
    if (command.kind === 'stop') {
      await Promise.all(
        tasks
          .filter((task) => task.state === 'running' || task.state === 'queued')
          .map((task) => cancel(task.id)),
      )
      return
    }
    if (command.kind === 'help') {
      setRoomDraft(project, '/')
      return
    }
    if (command.kind === 'reset') return resetAgent(command.roleId)
    setCommandError(t('swarm.commandUnknownReset', { role: command.roleId }))
  }
```

`reload`, `cancel`, `resetAgent` come from `useSwarmStore`; `tasks` too. `commandError` is local state rendered under the plate (`data-swarm-command-error`) and cleared on the next draft change.

The `/` menu: extend the existing menu branch — when `draft` matches `^\/\S*$` and the mention menu is closed, show `roomCommandMenu(draft)` entries; picking one inserts `entry.insert` and focuses. Keep the `@` menu behavior untouched.

i18n: `swarm.commandReload`, `swarm.commandReset`, `swarm.commandStop`, `swarm.commandHelp`, `swarm.commandUnknownReset` `'没有角色 @{role}'` in both dictionaries.

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm --dir desktop test && pnpm --dir desktop exec tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/state/roomCommands.ts desktop/tests/roomCommands.test.ts desktop/src/ui/composer/RoomComposer.tsx desktop/src/i18n/messages-swarm.ts
git commit -m "feat(desktop): room command table for reload, reset, stop and help"
```

---

### Task 10: Quote action and quote card in the room feed

**Files:**
- Modify: `desktop/src/ui/pages/RoomPage.tsx:44-139`, `desktop/src/ui/pages/RoomPage.module.css`, `desktop/src/i18n/messages-swarm.ts`

**Interfaces:**
- Consumes: `message.ref` (task 6), `setRoomQuote(project, ref)` (task 7).

- [ ] **Step 1: Implement the quote action and card**

`RoomPage` reads `const project = useSwarmStore((s) => s.loadedProject) ?? ''` and `const setQuote = useComposerStore((s) => s.setRoomQuote)`, plus `const [flash, setFlash] = useState<string | null>(null)`.

`jumpTo(id)`:

```tsx
  const jumpTo = (id: string) => {
    const node = feed.current?.querySelector(`[data-message-id="${id}"]`)
    if (node === null || node === undefined) return
    node.scrollIntoView({ block: 'center' })
    setFlash(id)
    window.setTimeout(() => setFlash((current) => (current === id ? null : current)), 1200)
  }
```

Pass `onQuote(message)` and `onJump(id)`, and `flash` into `MessageRow` — its props become `{ message, roles, numbers, flash, onQuote, onJump }`:

```tsx
onQuote={(message) =>
  setQuote(project, {
    id: message.id,
    author: message.author,
    text: message.text,
  })
}
```

`MessageRow` renders the hover action in the header and the quote card above the text:

```tsx
      <button
        type="button"
        className={css.quoteAction}
        data-swarm-quote
        aria-label={t('swarm.quoteAction')}
        onClick={() => onQuote(message)}
      >
        {t('swarm.quoteAction')}
      </button>
      ...
      {message.ref !== null && message.ref !== undefined && (
        <button
          type="button"
          className={css.quoteCard}
          data-swarm-quote-card
          onClick={() => onJump(message.ref!.id)}
        >
          <span className={css.quoteCardAuthor}>
            {roles.find((role) => role.id === message.ref!.author.id)?.name ??
              message.ref!.author.id}
          </span>
          <span className={css.quoteCardText}>{message.ref!.text}</span>
        </button>
      )}
```

The root gains `data-flash={flash || undefined}`; CSS animates it (`@keyframes` with `var(--dsw-alias-bg-layered)`), and `.quoteAction` is `opacity: 0` until `.message:hover` / `:focus-visible`.

i18n: `swarm.quoteAction` `'引用'` / `'Quote'` in both dictionaries.

- [ ] **Step 2: Verify**

Run: `pnpm --dir desktop exec tsc --noEmit && pnpm --dir desktop lint:tokens && pnpm --dir desktop test`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/ui/pages/RoomPage.tsx desktop/src/ui/pages/RoomPage.module.css desktop/src/i18n/messages-swarm.ts
git commit -m "feat(desktop): quote a room message into the composer and render quote cards"
```

---

### Task 11: E2E, changelog and the full gate

**Files:**
- Modify: `desktop/e2e/swarm.spec.ts`, `CHANGELOG.md`

- [ ] **Step 1: Write the e2e tests**

Append to `desktop/e2e/swarm.spec.ts`:

```ts
test('quoting a message sends its id with the post', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const source = page.locator('[data-swarm-message]', { hasText: '跑一个长任务' })
  await source.hover()
  await source.locator('[data-swarm-quote]').click()
  await expect(page.locator('[data-swarm-quote-strip]')).toContainText('跑一个长任务')

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 接着做')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-quote-strip]')).toHaveCount(0)
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const post = calls.filter((call) => call.type === 'swarm' && call.op === 'post').at(-1)
  assert.equal(post?.ref, 'm_seed')
})

test('the preview says a busy role will queue', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-input]').fill('@research 再来一个')
  await expect(page.locator('[data-swarm-preview]')).toContainText('排队')
})

test('/stop cancels every live task', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('/stop')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-task]')).toHaveAttribute('data-state', 'cancelled')
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.equal(
    calls.some((call) => call.type === 'swarm' && call.op === 'cancel' && call.taskId === 't_seed'),
    true,
  )
})

test('a slash message that is not a room command still posts', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('/usr/bin 在哪')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-message]', { hasText: '/usr/bin 在哪' })).toBeVisible()
})
```

- [ ] **Step 2: Run the e2e suite**

Run: `pnpm --dir desktop exec playwright test e2e/swarm.spec.ts`
Expected: PASS (all swarm specs, old and new).

- [ ] **Step 3: Add the changelog entry**

Under `## Unreleased` in `CHANGELOG.md`, add one bullet:

```markdown
- **Swarm composer parity** — quote a room message as context, attach images that fan out to every role a message delegates to, and use a room command table (`/reload`, `/reset`, `/stop`, `/help`).
```

- [ ] **Step 4: Run the full gate**

Run each, in order:

```bash
bash scripts/check.sh
pnpm --dir desktop build
pnpm --dir desktop lint:tokens
pnpm --dir desktop test
pnpm --dir desktop exec playwright test
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add desktop/e2e/swarm.spec.ts CHANGELOG.md
git commit -m "test(swarm): cover quoting, the busy preview and room commands"
```

---

## Self-Review Notes

- Spec §6.1 (quote), §6.2 (attachments), §6.3 (commands, minus `/roles`), §6.4 (`supportsVision` preview), §6.5 (preview), §7 (wire table), §8 (per-project state, Escape, busy wording) each map to a task above; §9's attach e2e is deliberately replaced by unit coverage (deviation noted).
- `/help` reopens the menu by resetting the draft to `/`; it posts nothing, matching the spec.
- `roomDraft` and `setRoomDraft` are removed rather than aliased — no compatibility shim.
