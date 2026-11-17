from zenagent.types import UserMessage, message_from_json


def test_goal_flag_roundtrip():
    message = UserMessage(content="continue", timestamp=1, goal=True)
    payload = message.to_json()
    assert payload["goal"] is True
    assert message_from_json(payload).goal is True


async def test_agent_prompt_marks_the_user_message(runtime_factory):
    runtime = await runtime_factory()
    await runtime.agent.prompt("continue", goal=True)
    assert runtime.agent.messages[0].goal is True
