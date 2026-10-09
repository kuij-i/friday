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
