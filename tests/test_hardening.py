"""Regression tests for robustness / safety hardening."""
import json
import time
from datetime import datetime

from friday.agent import _HISTORY_CHAR_BUDGET, Agent
from friday.channels.cli import _approval_preview
from friday.config import Config
from friday.memory import Memory, next_daily_run
from friday.scheduler import Scheduler
from friday.tools.base import Risk
from friday.tools.files import make_file_tools
from friday.tools.schedule import make_schedule_tools
from friday.tools.security import make_security_tools
from friday.tools.shell import make_exec_tools
from tests.fakes import FakeClient, FakeResponse, TextBlock


def tool_map(tools):
    return {t.name: t for t in tools}


# ── sandbox ─────────────────────────────────────────────────────────────
def test_hash_file_cannot_escape_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("s3cret")
    t = tool_map(make_security_tools(ws))
    r = t["hash_file"].handler(path="../secret.txt")
    assert r.is_error and "outside" in r.content


def test_symlink_escape_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("s3cret")
    (ws / "link").symlink_to(tmp_path / "secret.txt")
    t = tool_map(make_file_tools(ws))
    assert t["read_file"].handler(path="link").is_error


def test_read_file_large_is_bounded(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.txt").write_text("a" * 1_000_000)
    r = tool_map(make_file_tools(ws))["read_file"].handler(path="big.txt")
    assert not r.is_error and r.content.endswith("[truncated]") and len(r.content) < 70_000


# ── exec ────────────────────────────────────────────────────────────────
def test_shell_has_no_stdin(tmp_path):
    t = tool_map(make_exec_tools(tmp_path, timeout=10))
    r = t["run_shell"].handler(command="cat")  # would hang forever reading a tty
    assert "exit code: 0" in r.content


def test_shell_timeout_kills_background_children(tmp_path):
    t = tool_map(make_exec_tools(tmp_path, timeout=1))
    start = time.monotonic()
    r = t["run_shell"].handler(command="sleep 30 & sleep 30")
    assert r.is_error and "timed out" in r.content
    assert time.monotonic() - start < 10


def test_shell_output_is_bounded(tmp_path):
    t = tool_map(make_exec_tools(tmp_path, timeout=30))
    r = t["run_shell"].handler(command="yes | head -c 5000000")
    assert len(r.content) < 25_000


# ── memory ──────────────────────────────────────────────────────────────
def test_forget_treats_wildcards_literally(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    mem.remember_fact("a", "likes tea")
    mem.remember_fact("b", "100% sure")
    assert mem.forget_fact("%") == 1
    assert mem.forget_fact("_") == 0
    assert mem.forget_fact("   ") == 0
    assert [f["content"] for f in mem.recall_facts()] == ["likes tea"]
    mem.close()


def test_context_blob_is_bounded(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    for i in range(40):
        mem.remember_fact("t", f"{i} " + "x" * 7_000)
    for i in range(200):
        mem.set_preference(f"k{i}", "v" * 7_000)
    blob = mem.context_blob()
    assert len(blob) < 26_000 and "omitted" in blob
    mem.close()


def test_settings_roundtrip_and_hidden_from_prompt(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    mem.set_setting("telegram_owner_id", "42")
    assert mem.get_setting("telegram_owner_id") == "42"
    assert "42" not in mem.context_blob()
    mem.close()


def test_migration_adds_daily_time(tmp_path):
    import sqlite3

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, "
        "text TEXT NOT NULL, next_run REAL NOT NULL, interval REAL, active INTEGER NOT NULL DEFAULT 1, "
        "created REAL NOT NULL)"
    )
    conn.commit()
    conn.close()
    mem = Memory(db)
    mem.add_reminder("tg:1", "x", time.time() + 60, 86_400, daily_time="08:00")
    assert mem.list_reminders()[0]["daily_time"] == "08:00"
    mem.close()


# ── scheduling ──────────────────────────────────────────────────────────
def test_schedule_rejects_non_finite_and_huge(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    t = tool_map(make_schedule_tools(mem, "tg:1"))
    for bad in (float("inf"), float("nan"), -5, 1e12, "soon"):
        assert t["schedule_reminder"].handler(text="x", delay_minutes=bad).is_error, bad
    assert t["schedule_reminder"].handler(text="x", delay_minutes=1, repeat_minutes="often").is_error
    mem.close()


def test_daily_reminder_keeps_wall_clock_time(tmp_path):
    after = time.time()
    nxt = next_daily_run("07:15", after)
    assert nxt > after and nxt - after <= 86_400 + 3_600
    dt = datetime.fromtimestamp(nxt)
    assert (dt.hour, dt.minute) == (7, 15)
    # Rescheduling after it fires lands on 07:15 the next day.
    again = datetime.fromtimestamp(next_daily_run("07:15", nxt))
    assert (again.hour, again.minute) == (7, 15) and again.date() > dt.date()


def test_scheduler_reschedules_daily_by_wall_clock(tmp_path):
    import asyncio

    mem = Memory(tmp_path / "db.sqlite")
    mem.add_reminder("tg:1", "standup", time.time() - 1, 86_400, daily_time="09:30")

    async def deliver(_c, _t):
        pass

    asyncio.run(Scheduler(mem, deliver)._tick())
    r = mem.list_reminders()[0]
    dt = datetime.fromtimestamp(r["next_run"])
    assert (dt.hour, dt.minute) == (9, 30) and r["next_run"] > time.time()
    mem.close()


# ── agent ───────────────────────────────────────────────────────────────
def _agent(tmp_path, responses):
    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.workspace = tmp_path / "ws"
    cfg.workspace.mkdir(exist_ok=True)
    cfg.enable_web_search = False
    mem = Memory(tmp_path / "db.sqlite")
    client = FakeClient(responses)
    return Agent(cfg, mem, client=client), mem, client


def test_send_never_raises_on_storage_failure(tmp_path):
    agent, mem, _ = _agent(tmp_path, [])
    mem.close()  # every DB call now raises sqlite3.ProgrammingError
    out = agent.send("hello")
    assert isinstance(out, str) and "went wrong" in out


def test_history_is_bounded(tmp_path):
    agent, mem, client = _agent(tmp_path, [FakeResponse([TextBlock("ok")], "end_turn")])
    for _ in range(10):
        mem.log_message("cli", "user", "u" * 100_000)
        mem.log_message("cli", "assistant", "a" * 100_000)
    assert agent.send("latest") == "ok"
    sent = client.messages.calls[0]["messages"]
    assert sum(len(m["content"]) for m in sent) <= _HISTORY_CHAR_BUDGET
    assert sent[0]["role"] == "user" and sent[-1]["content"] == "latest"
    mem.close()


# ── CLI approval prompt ─────────────────────────────────────────────────
def test_dangerous_preview_is_never_truncated():
    cmd = "echo hi; " * 400 + "rm -rf ~"
    preview = _approval_preview({"command": cmd}, Risk.DANGEROUS)
    assert json.loads(preview)["command"] == cmd


def test_low_preview_may_truncate_but_says_so():
    preview = _approval_preview({"content": "x" * 5_000}, Risk.LOW)
    assert "more chars not shown" in preview
