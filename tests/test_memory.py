from friday.memory import Memory


def test_facts_roundtrip(tmp_path):
    m = Memory(tmp_path / "t.db")
    m.remember_fact("projects", "main project is Friday")
    m.remember_fact("projects", "main project is Friday")  # dedup
    facts = m.recall_facts("projects")
    assert len(facts) == 1
    assert facts[0]["content"] == "main project is Friday"
    m.close()


def test_preferences(tmp_path):
    m = Memory(tmp_path / "t.db")
    m.set_preference("reply_style", "terse")
    m.set_preference("reply_style", "detailed")  # upsert
    assert m.get_preferences()["reply_style"] == "detailed"
    m.close()


def test_message_log_and_order(tmp_path):
    m = Memory(tmp_path / "t.db")
    m.log_message("cli", "user", "hello")
    m.log_message("cli", "assistant", "hi")
    m.log_message("tg:1", "user", "other channel")
    msgs = m.recent_messages("cli")
    assert [x["role"] for x in msgs] == ["user", "assistant"]
    assert m.clear_channel("cli") == 2
    assert m.recent_messages("cli") == []
    assert len(m.recent_messages("tg:1")) == 1
    m.close()


def test_context_blob(tmp_path):
    m = Memory(tmp_path / "t.db")
    assert "no stored memory" in m.context_blob()
    m.set_preference("tz", "UTC")
    m.remember_fact("system", "runs on Windows")
    blob = m.context_blob()
    assert "tz: UTC" in blob and "runs on Windows" in blob
    m.close()


def test_fact_content_capped(tmp_path):
    m = Memory(tmp_path / "t.db")
    m.remember_fact("big", "x" * 100_000)
    stored = m.recall_facts("big")[0]["content"]
    assert len(stored) <= 8_000
    m.close()


def test_empty_fact_ignored(tmp_path):
    m = Memory(tmp_path / "t.db")
    m.remember_fact("", "")
    m.remember_fact("topic", "   ")
    assert m.recall_facts() == []
    m.close()


def test_reminder_lifecycle(tmp_path):
    m = Memory(tmp_path / "t.db")
    rid = m.add_reminder("cli", "task", next_run=1.0, interval=None)
    assert rid > 0
    assert len(m.due_reminders(now=2.0)) == 1
    assert m.due_reminders(now=0.5) == []
    m.reschedule_or_close(rid, None, fired_at=2.0)
    assert m.due_reminders(now=10.0) == []  # one-off closed
    # scoped cancel guards channel
    rid2 = m.add_reminder("cli", "task2", next_run=1.0)
    assert not m.cancel_reminder(rid2, channel="other")
    assert m.cancel_reminder(rid2, channel="cli")
    m.close()


def test_in_memory_db():
    m = Memory(":memory:")
    m.remember_fact("t", "c")
    assert m.recall_facts("t")[0]["content"] == "c"
    m.close()
