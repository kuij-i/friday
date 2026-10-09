
from friday.memory import Memory
from friday.tools.files import make_file_tools
from friday.tools.schedule import make_schedule_tools
from friday.tools.security import make_security_tools


def tool_map(tools):
    return {t.name: t for t in tools}


# ── file tools ─────────────────────────────────────────────────────────
def test_file_write_read_list_roundtrip(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    t = tool_map(make_file_tools(ws))
    assert not t["write_file"].handler(path="a/b.txt", content="hello").is_error
    r = t["read_file"].handler(path="a/b.txt")
    assert r.content == "hello" and not r.is_error
    listing = t["list_dir"].handler(path="a")
    assert "b.txt" in listing.content


def test_file_traversal_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    t = tool_map(make_file_tools(ws))
    r = t["write_file"].handler(path="../escape.txt", content="x")
    assert r.is_error
    assert not (tmp_path / "escape.txt").exists()
    r2 = t["read_file"].handler(path="../../etc/passwd")
    assert r2.is_error


def test_file_binary_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bin").write_bytes(b"\x00\x01\x02binary")
    t = tool_map(make_file_tools(ws))
    r = t["read_file"].handler(path="bin")
    assert r.is_error and "binary" in r.content


def test_file_write_size_cap(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    t = tool_map(make_file_tools(ws))
    r = t["write_file"].handler(path="big.txt", content="x" * 6_000_000)
    assert r.is_error and "Refusing" in r.content


def test_read_missing_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    t = tool_map(make_file_tools(ws))
    assert t["read_file"].handler(path="nope.txt").is_error


# ── security tools ───────────────────────────────────────────────────────
def test_hash_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("abc")
    t = tool_map(make_security_tools(ws))
    r = t["hash_file"].handler(path="f.txt")
    # sha256("abc")
    assert "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" in r.content


def test_hash_unknown_algo(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("abc")
    t = tool_map(make_security_tools(ws))
    assert t["hash_file"].handler(path="f.txt", algorithm="nope").is_error


def test_system_info_runs(tmp_path):
    t = tool_map(make_security_tools(tmp_path))
    r = t["system_info"].handler()
    assert "platform:" in r.content and not r.is_error


# ── schedule tools ───────────────────────────────────────────────────────
def test_schedule_delay_and_cancel(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    t = tool_map(make_schedule_tools(mem, "tg:1"))
    r = t["schedule_reminder"].handler(text="ping", delay_minutes=5)
    assert not r.is_error and "#" in r.content
    rid = int(r.content.split("#", 1)[1].split()[0])
    assert "ping" in t["list_reminders"].handler().content
    assert not t["cancel_reminder"].handler(reminder_id=rid).is_error
    assert "No active reminders" in t["list_reminders"].handler().content
    mem.close()


def test_schedule_requires_timing(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    t = tool_map(make_schedule_tools(mem, "tg:1"))
    assert t["schedule_reminder"].handler(text="x").is_error
    assert t["schedule_reminder"].handler(text="", delay_minutes=1).is_error
    mem.close()


def test_schedule_daily_time_validation(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    t = tool_map(make_schedule_tools(mem, "tg:1"))
    assert t["schedule_reminder"].handler(text="x", daily_time="99:99").is_error
    assert not t["schedule_reminder"].handler(text="x", daily_time="08:30").is_error
    mem.close()
