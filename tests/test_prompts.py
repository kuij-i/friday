"""System prompt: channel notes apply by channel kind."""
from friday.memory import Memory
from friday.prompts import _channel_kind, build_system_prompt

TG_NOTE = "You are being used from Telegram on the user's iPhone — keep replies short."
CLI_NOTE = "You are being used from a desktop terminal."


def _prompt(tmp_path, channel):
    mem = Memory(tmp_path / "db.sqlite")
    try:
        return build_system_prompt(mem, channel, web_search=False)
    finally:
        mem.close()


def test_channel_kind():
    assert _channel_kind("cli") == "cli"
    assert _channel_kind("telegram") == "telegram"
    assert _channel_kind("tg:12345") == "telegram"
    assert _channel_kind("tg:-100123") == "telegram"
    assert _channel_kind("web") == ""
    assert _channel_kind("") == ""


def test_real_telegram_chats_get_the_telegram_note(tmp_path):
    for channel in ("tg:12345", "tg:-100123", "telegram"):
        prompt = _prompt(tmp_path, channel)
        assert TG_NOTE in prompt and CLI_NOTE not in prompt


def test_cli_gets_the_cli_note(tmp_path):
    prompt = _prompt(tmp_path, "cli")
    assert CLI_NOTE in prompt and TG_NOTE not in prompt


def test_unknown_channel_gets_no_note(tmp_path):
    prompt = _prompt(tmp_path, "web")
    assert CLI_NOTE not in prompt and TG_NOTE not in prompt
