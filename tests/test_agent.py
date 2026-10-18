import anthropic

from friday.agent import Agent
from friday.config import Config
from friday.memory import Memory
from tests.fakes import FakeClient, FakeResponse, StopDetails, TextBlock, ToolUseBlock


def make_config(tmp_path, **over):
    cfg = Config()
    cfg.anthropic_api_key = "test"
    cfg.workspace = tmp_path / "ws"
    cfg.db_path = tmp_path / "db.sqlite"
    cfg.enable_web_search = False
    cfg.ensure_dirs()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def agent_with(tmp_path, responses, approver=None, **over):
    cfg = make_config(tmp_path, **over)
    mem = Memory(cfg.db_path)
    return Agent(cfg, mem, channel="cli", approver=approver, client=FakeClient(responses)), mem


def test_plain_text_turn(tmp_path):
    agent, mem = agent_with(tmp_path, [FakeResponse([TextBlock("hello there")], "end_turn")])
    assert agent.send("hi") == "hello there"
    # history logged both sides
    assert mem.recent_messages("cli")[-1]["content"] == "hello there"
    mem.close()


def test_empty_message_short_circuits(tmp_path):
    agent, mem = agent_with(tmp_path, [])
    assert "didn't catch" in agent.send("   ")
    mem.close()


def test_tool_call_then_answer(tmp_path):
    responses = [
        FakeResponse([ToolUseBlock("remember", {"topic": "t", "content": "c"})], "tool_use"),
        FakeResponse([TextBlock("done")], "end_turn"),
    ]
    agent, mem = agent_with(tmp_path, responses, approver=lambda *a: (True, "ok"))
    assert agent.send("remember c") == "done"
    assert mem.recall_facts("t")[0]["content"] == "c"
    mem.close()


def test_declined_tool_is_reported_not_raised(tmp_path):
    responses = [
        FakeResponse([ToolUseBlock("write_file", {"path": "x.txt", "content": "y"})], "tool_use"),
        FakeResponse([TextBlock("ok, skipped")], "end_turn"),
    ]
    agent, mem = agent_with(tmp_path, responses, approver=lambda *a: (False, "declined"))
    assert agent.send("write x") == "ok, skipped"
    # file must not exist because approval was denied
    assert not (agent.config.workspace / "x.txt").exists()
    mem.close()


def test_missing_required_arg_is_handled(tmp_path):
    responses = [
        FakeResponse([ToolUseBlock("write_file", {"path": "x.txt"})], "tool_use"),
        FakeResponse([TextBlock("recovered")], "end_turn"),
    ]
    agent, mem = agent_with(tmp_path, responses, approver=lambda *a: (True, "ok"))
    assert agent.send("oops") == "recovered"
    mem.close()


def test_refusal(tmp_path):
    responses = [FakeResponse([], "refusal", StopDetails(category="cyber"))]
    agent, mem = agent_with(tmp_path, responses)
    out = agent.send("bad")
    assert "can't help" in out and "cyber" in out
    mem.close()


def test_max_tokens_continuation(tmp_path):
    responses = [
        FakeResponse([TextBlock("part1 ")], "max_tokens"),
        FakeResponse([TextBlock("part2")], "end_turn"),
    ]
    agent, mem = agent_with(tmp_path, responses)
    assert agent.send("long") == "part2"
    mem.close()


def test_api_error_becomes_message(tmp_path):
    class BoomClient:
        class messages:
            @staticmethod
            def create(**k):
                raise anthropic.APIConnectionError(request=None)

    cfg = make_config(tmp_path)
    mem = Memory(cfg.db_path)
    agent = Agent(cfg, mem, channel="cli", client=BoomClient())
    assert "couldn't reach" in agent.send("hi").lower()
    mem.close()


def test_iteration_cap(tmp_path):
    # Always ask for a tool → loop until cap, then a graceful message.
    responses = [
        FakeResponse([ToolUseBlock("recall", {})], "tool_use") for _ in range(50)
    ]
    agent, mem = agent_with(tmp_path, responses, approver=lambda *a: (True, "ok"), max_iterations=5)
    out = agent.send("loop")
    assert "step limit" in out
    mem.close()
