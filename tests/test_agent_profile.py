"""AgentProfile: the identity a run recorder stamps on its rows."""

from __future__ import annotations

from zenagent.app.agent_profile import AgentProfile


def test_profile_builds_a_run_scope():
    profile = AgentProfile(id="research", kind="coding", label="Research")
    assert profile.run_scope() == {
        "kind": "coding",
        "agentId": "research",
        "label": "Research",
    }
    assert profile.run_scope(workItemId="w1") == {
        "kind": "coding",
        "agentId": "research",
        "label": "Research",
        "workItemId": "w1",
    }
