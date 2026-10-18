"""NVIDIA provider: translation units plus a real-SDK loop against a localhost mock.

The mock speaks NVIDIA's OpenAI-compatible /v1/chat/completions, so these run
offline but still exercise the real `openai` client end to end.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from friday.agent import Agent
from friday.config import Config
from friday.memory import Memory
from friday.providers import (
    NvidiaClient,
    TextBlock,
    ToolUseBlock,
    from_openai_response,
    to_openai_messages,
    to_openai_tools,
)


# ── translation ──────────────────────────────────────────────────────────
def test_messages_translate_tool_round_trip():
    msgs = [
        {"role": "user", "content": "make a note"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "s"},  # Claude-only, dropped
            TextBlock("Writing."),
            ToolUseBlock(id="call_1", name="write_file", input={"path": "a.txt", "content": "hi"}),
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "Wrote 2 chars", "is_error": False},
        ]},
    ]
    out = to_openai_messages("be helpful", msgs)
    assert out[0] == {"role": "system", "content": "be helpful"}
    assert out[1] == {"role": "user", "content": "make a note"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "Writing."
    call = out[2]["tool_calls"][0]
    assert call["id"] == "call_1" and call["function"]["name"] == "write_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt", "content": "hi"}
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": "Wrote 2 chars"}


def test_tool_errors_are_marked():
    out = to_openai_messages("", [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c", "content": "boom", "is_error": True}]}])
    assert out == [{"role": "tool", "tool_call_id": "c", "content": "ERROR: boom"}]


def test_tools_translate_and_server_tools_are_dropped():
    tools = [
        {"name": "read_file", "description": "Read", "input_schema": {"type": "object", "properties": {}}},
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    ]
    assert to_openai_tools(tools) == [{"type": "function", "function": {
        "name": "read_file", "description": "Read", "parameters": {"type": "object", "properties": {}}}}]


def _completion(content=None, tool_calls=None, finish="stop"):
    calls = [SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a)) for i, n, a in tool_calls or []]
    msg = SimpleNamespace(content=content, tool_calls=calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def test_response_mapping():
    r = from_openai_response(_completion("<think>hmm</think>Hello", finish="stop"))
    assert r.stop_reason == "end_turn" and r.content == [TextBlock("Hello")]
    r = from_openai_response(_completion(None, [("c1", "recall", '{"topic": "x"}')], finish="tool_calls"))
    assert r.stop_reason == "tool_use" and r.content[0].input == {"topic": "x"}
    # Some servers say "stop" even when they return tool calls.
    assert from_openai_response(_completion(None, [("c", "recall", "{}")], finish="stop")).stop_reason == "tool_use"
    # Malformed arguments are passed through; the agent rejects non-object input.
    assert from_openai_response(_completion(None, [("c", "recall", "{oops")])).content[0].input == "{oops"
    assert from_openai_response(_completion("x", finish="length")).stop_reason == "max_tokens"
    assert from_openai_response(_completion("", finish="content_filter")).stop_reason == "refusal"


# ── config ───────────────────────────────────────────────────────────────
def test_provider_auto_detects_nvidia(monkeypatch):
    monkeypatch.setenv("FRIDAY_PROVIDER", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-...")  # the .env.example placeholder
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-real")
    monkeypatch.delenv("FRIDAY_MODEL", raising=False)
    cfg = Config()
    assert cfg.provider == "nvidia"
    assert cfg.model == "nvidia/nemotron-3-super-120b-a12b"
    assert not cfg.web_search_available
    assert cfg.validate() == []


def test_provider_validation(monkeypatch):
    monkeypatch.setenv("FRIDAY_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    assert any("NVIDIA_API_KEY" in p for p in Config().validate())
    monkeypatch.setenv("FRIDAY_PROVIDER", "openrouter")
    assert any("FRIDAY_PROVIDER" in p for p in Config().validate())


# ── real openai SDK against a localhost mock of NVIDIA ───────────────────
def _chat(content=None, tool_calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": a}}
                             for i, n, a in tool_calls]
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@pytest.fixture
def nvidia_mock():
    requests: list[dict] = []
    script: list[tuple[int, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):  # noqa: N802 — http.server API
            requests.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            status, payload = script.pop(0) if script else (500, {"error": {"message": "no script"}})
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", script, requests
    server.shutdown()


def _agent(tmp_path, base_url):
    import openai

    cfg = Config()
    cfg.provider, cfg.model, cfg.nvidia_api_key = "nvidia", "nvidia/nemotron-3-super-120b-a12b", "nvapi-test"
    cfg.workspace, cfg.db_path = tmp_path / "ws", tmp_path / "db.sqlite"
    cfg.ensure_dirs()
    mem = Memory(cfg.db_path)
    sdk = openai.OpenAI(api_key="nvapi-test", base_url=base_url, max_retries=0)
    client = NvidiaClient("nvapi-test", client=sdk)
    return Agent(cfg, mem, approver=lambda *_: (True, "test"), client=client), mem


def test_tool_loop_through_real_openai_sdk(tmp_path, nvidia_mock):
    base_url, script, requests = nvidia_mock
    script += [
        (200, _chat("Saving it.", [("call_9", "write_file", '{"path": "n.txt", "content": "hi"}')], "tool_calls")),
        (200, _chat("Done — saved n.txt.")),
    ]
    agent, mem = _agent(tmp_path, base_url)
    assert agent.send("save a note") == "Done — saved n.txt."
    assert (tmp_path / "ws" / "n.txt").read_text() == "hi"

    first, second = requests
    assert first["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert first["max_tokens"] <= 4096
    assert first["messages"][0]["role"] == "system" and "web search" not in first["messages"][0]["content"]
    names = {t["function"]["name"] for t in first["tools"]}
    assert "write_file" in names and "web_search" not in names
    assert "thinking" not in first and "output_config" not in first
    assert second["messages"][-2]["tool_calls"][0]["id"] == "call_9"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "call_9", "content": "Wrote 2 chars to n.txt"}
    mem.close()


@pytest.mark.parametrize("status,message,expect", [
    (401, "Unauthorized", "NVIDIA_API_KEY"),
    (403, "Authorization failed", "NVIDIA_API_KEY"),  # what NVIDIA really returns for a bad key
    (404, "Not found", "doesn't serve the model"),
    (400, "Model does not support tools", "doesn't support tool calling"),
    (500, "boom", "server error"),
])
def test_api_errors_become_friendly_text(tmp_path, nvidia_mock, status, message, expect):
    base_url, script, _ = nvidia_mock
    script.append((status, {"error": {"message": message, "type": "error"}}))
    agent, mem = _agent(tmp_path, base_url)
    assert expect in agent.send("hi")
    mem.close()


def test_unreachable_nvidia_becomes_text(tmp_path):
    agent, mem = _agent(tmp_path, "http://127.0.0.1:9/v1")
    assert "couldn't reach the NVIDIA API" in agent.send("hi")
    mem.close()
