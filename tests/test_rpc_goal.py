import asyncio
import json

from zenagent.cli.rpc import RpcServer


async def test_status_includes_goal(services):
    runtime = services.runtime
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix")

    queue = [
        json.dumps({"type": "status", "id": "s"}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ]
    writes: list[dict] = []

    async def read_line():
        await asyncio.sleep(0)
        return queue.pop(0) if queue else None

    def write_line(line: str) -> None:
        writes.append(json.loads(line))

    server = RpcServer(
        runtime.agent,
        read_line,
        write_line,
        runtime=runtime,
        chat=services.chat,
    )
    await server.run()
    status = next(m for m in writes if m.get("id") == "s")
    assert status["goal"]["objective"] == "fix"
    assert status["status"]["goal"]["objective"] == "fix"
