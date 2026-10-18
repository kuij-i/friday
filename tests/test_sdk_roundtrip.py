"""Drive the agent through the REAL Anthropic SDK against a localhost mock.

The fake-client tests can't catch SDK-level breakage (an SDK too old for a
parameter, content blocks that don't serialize back). This serves scripted
Messages API responses from 127.0.0.1, so it stays offline.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import pytest

from friday.agent import Agent
from friday.config import Config
from friday.memory import Memory


def _message(content, stop):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.fixture
def mock_api():
    requests: list[dict] = []
    script: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):  # noqa: N802 — http.server API
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append(body)
            if script:
                status, payload = 200, script.pop(0)
            else:
                status, payload = 500, {"type": "error", "error": {"type": "api_error", "message": "x"}}
            if "error" in payload:
                status = payload.pop("_status", status)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", script, requests
    server.shutdown()


def _agent(tmp_path, base_url):
    cfg = Config()
    cfg.anthropic_api_key = "test-key"
    cfg.workspace, cfg.db_path = tmp_path / "ws", tmp_path / "db.sqlite"
    cfg.ensure_dirs()
    mem = Memory(cfg.db_path)
    client = anthropic.Anthropic(api_key="test-key", base_url=base_url, max_retries=0)
    return Agent(cfg, mem, approver=lambda *_: (True, "test"), client=client), mem


def test_tool_loop_through_real_sdk(tmp_path, mock_api):
    base_url, script, requests = mock_api
    script += [
        _message([
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            {"type": "text", "text": "Writing."},
            {"type": "tool_use", "id": "toolu_1", "name": "write_file",
             "input": {"path": "note.txt", "content": "hi"}},
        ], "tool_use"),
        _message([{"type": "text", "text": "All done."}], "end_turn"),
    ]
    agent, mem = _agent(tmp_path, base_url)
    assert agent.send("make a note") == "All done."
    assert (tmp_path / "ws" / "note.txt").read_text() == "hi"

    first, second = requests
    assert first["model"] == "claude-opus-5-5"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": agent.config.effort}
    assert any(t.get("type") == "web_search_20260209" for t in first["tools"])
    # The assistant turn (thinking block included) is echoed back unchanged.
    assert second["messages"][1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1" and not result["is_error"]
    mem.close()


def test_api_errors_become_text(tmp_path, mock_api):
    base_url, script, _ = mock_api
    script.append({"_status": 401, "type": "error",
                   "error": {"type": "authentication_error", "message": "bad key"}})
    agent, mem = _agent(tmp_path, base_url)
    assert "API key was rejected" in agent.send("hi")
    mem.close()


def test_unreachable_api_becomes_text(tmp_path):
    agent, mem = _agent(tmp_path, "http://127.0.0.1:9")  # nothing listens on the discard port
    assert "couldn't reach" in agent.send("hi").lower()
    mem.close()
