import asyncio
import json

from zenagent import settings
from zenagent.cli.rpc import RpcServer


async def _run(services, requests: list[dict]) -> list[dict]:
    queue = [json.dumps(request) for request in requests]
    queue.append(json.dumps({"type": "shutdown", "id": "9"}))
    writes: list[dict] = []

    async def read_line():
        await asyncio.sleep(0)
        return queue.pop(0) if queue else None

    def write_line(line: str) -> None:
        writes.append(json.loads(line))

    server = RpcServer(
        services.runtime.agent,
        read_line,
        write_line,
        runtime=services.runtime,
        chat=services.chat,
    )
    await server.run()
    return writes


async def test_prefs_roundtrip(services, monkeypatch, tmp_path):
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.clear_cache()
    try:
        writes = await _run(
            services,
            [
                {"type": "prefs", "id": "p1"},
                {
                    "type": "set_pref",
                    "id": "p2",
                    "name": "goal_pause_on_session_switch",
                    "value": True,
                },
                {"type": "prefs", "id": "p3"},
            ],
        )
        first = next(m for m in writes if m.get("id") == "p1")
        second = next(m for m in writes if m.get("id") == "p3")
        assert first["prefs"]["goal_pause_on_session_switch"] is False
        assert second["prefs"]["goal_pause_on_session_switch"] is True
    finally:
        settings.clear_cache()


async def test_unknown_pref_is_refused(services, monkeypatch, tmp_path):
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.clear_cache()
    try:
        writes = await _run(
            services,
            [{"type": "set_pref", "id": "p1", "name": "not_a_pref", "value": True}],
        )
        answer = next(m for m in writes if m.get("id") == "p1")
        assert answer["type"] == "error"
        assert "unknown pref" in answer["message"]
    finally:
        settings.clear_cache()
