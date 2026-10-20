"""Network-free checks for the Telegram bridge's ownership, pairing and delivery logic."""
import asyncio
import logging
from types import SimpleNamespace

import pytest

from friday.channels import pairing, telegram_bot
from friday.channels.pairing import LEGACY_SETTING, PAIRED_SETTING
from friday.channels.telegram_bot import _NOT_PAIRED, TelegramFriday
from friday.config import Config


def _cfg(tmp_path, owner=""):
    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.telegram_owner_id = owner
    cfg.db_path = tmp_path / "db.sqlite"
    cfg.workspace = tmp_path / "ws"
    return cfg


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []
        self.voice = self.audio = None

    async def reply_text(self, text):
        self.replies.append(text)


def _update(uid, chat_type="private", chat_id=None, text="hi"):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=uid if chat_id is None else chat_id, type=chat_type),
        effective_message=FakeMessage(text),
    )


def _ctx(*args):
    async def send_chat_action(**_kw):
        return None

    return SimpleNamespace(args=list(args), bot=SimpleNamespace(send_chat_action=send_chat_action))


class FakeAgent:
    def __init__(self, *_a, **_kw):
        self.seen: list[str] = []

    def send(self, text):
        self.seen.append(text)
        return f"echo: {text}"


@pytest.fixture
def no_agent(monkeypatch):
    def boom(*_a, **_kw):
        raise AssertionError("Agent must not be created for an unauthorized update")

    monkeypatch.setattr(telegram_bot, "Agent", boom)


@pytest.fixture
def fake_agent(monkeypatch):
    monkeypatch.setattr(telegram_bot, "Agent", FakeAgent)


@pytest.fixture
def no_scheduler(monkeypatch):
    started = []
    monkeypatch.setattr(TelegramFriday, "_start_scheduler", lambda self: started.append(self.owner_id))
    return started


def _pair(bot, uid, code, chat_type="private"):
    upd = _update(uid, chat_type=chat_type)
    asyncio.run(bot.pair(upd, _ctx(code)))
    return upd.effective_message.replies


def _say(bot, uid, text="hi", chat_type="private"):
    upd = _update(uid, chat_type=chat_type, text=text)
    asyncio.run(bot.on_message(upd, _ctx()))
    return upd.effective_message.replies


# ── no owner → pairing mode ─────────────────────────────────────────────
def test_no_owner_is_not_authorized_and_never_builds_an_agent(tmp_path, no_agent, monkeypatch):
    bot = TelegramFriday(_cfg(tmp_path))
    monkeypatch.setattr(bot.memory, "context_blob", lambda *a, **k: pytest.fail("memory read"))
    assert bot.owner_id is None and not bot._authorized(_update(111))
    assert _say(bot, 111) == [_NOT_PAIRED]
    upd = _update(111)
    asyncio.run(bot.memory_cmd(upd, _ctx()))
    assert upd.effective_message.replies == [_NOT_PAIRED]
    assert bot._agents == {}
    bot.memory.close()


def test_correct_code_pairs_and_persists(tmp_path, fake_agent, no_scheduler, capsys):
    bot = TelegramFriday(_cfg(tmp_path))
    code = bot._pairing.display
    assert code in bot.pairing_banner()
    replies = _pair(bot, 111, code.lower().replace("-", " "))  # typing is forgiving
    assert "Paired" in replies[0] and "111" not in replies[0]
    assert bot.owner_id == 111 and bot.memory.get_setting(PAIRED_SETTING) == "111"
    assert "Paired with Telegram user 111. Pin TELEGRAM_OWNER_ID=111 in .env" in capsys.readouterr().out
    assert no_scheduler == [111]
    assert _say(bot, 111, "hello") == ["echo: hello"]
    assert _say(bot, 222) == ["Not authorized."]
    bot.memory.close()


def test_wrong_code_rejected(tmp_path, no_scheduler):
    bot = TelegramFriday(_cfg(tmp_path))
    assert _pair(bot, 111, "AAAA-AAAA") == ["That code is wrong or has expired."]
    assert bot.owner_id is None and bot.memory.get_setting(PAIRED_SETTING) is None
    bot.memory.close()


def test_sixth_attempt_rejected_even_with_right_code(tmp_path, no_scheduler, caplog):
    bot = TelegramFriday(_cfg(tmp_path))
    code = bot._pairing.display
    wrong = "2222-2222" if code != "2222-2222" else "3333-3333"
    with caplog.at_level(logging.WARNING, logger="friday.telegram"):
        for uid in (1, 2, 3, 4, 5):  # attempts are counted in total, not per user
            _pair(bot, uid, wrong)
    assert any("invalidated" in r.message for r in caplog.records)
    replies = _pair(bot, 111, code)
    assert replies == ["Pairing is closed. Restart Friday on the computer to get a new code."]
    assert bot.owner_id is None
    bot.memory.close()


def test_expired_code_rejected(tmp_path, no_scheduler, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(pairing.time, "monotonic", lambda: now[0])
    bot = TelegramFriday(_cfg(tmp_path))
    now[0] += pairing.CODE_TTL_SECONDS + 1
    assert _pair(bot, 111, bot._pairing.display) == ["That code is wrong or has expired."]
    assert bot.owner_id is None
    bot.memory.close()


def test_code_is_single_use(tmp_path, no_scheduler):
    code = pairing.PairingCode()
    assert code.check(code.display) is pairing.PairResult.OK
    assert code.check(code.display) is pairing.PairResult.USED
    # And in the bot: once paired, the same code can't pair anyone else.
    bot = TelegramFriday(_cfg(tmp_path))
    display = bot._pairing.display
    _pair(bot, 111, display)
    assert _pair(bot, 222, display) == ["Not authorized."] and bot.owner_id == 111
    bot.memory.close()


def test_pair_in_group_chat_rejected(tmp_path, no_scheduler):
    bot = TelegramFriday(_cfg(tmp_path))
    code = bot._pairing.display
    assert _pair(bot, 111, code, chat_type="group") == []
    assert bot.owner_id is None and bot._pairing.open
    bot.memory.close()


def test_legacy_claim_is_not_honoured(tmp_path, no_agent, caplog):
    cfg = _cfg(tmp_path)
    from friday.memory import Memory

    mem = Memory(cfg.db_path)
    mem.set_setting(LEGACY_SETTING, "666")
    mem.close()
    with caplog.at_level(logging.WARNING, logger="friday.telegram"):
        bot = TelegramFriday(cfg)
    assert bot.owner_id is None and bot._pairing is not None
    assert any(
        "Found an owner id 666 claimed by an older Friday version" in r.message
        and "--reset-owner" in r.message
        for r in caplog.records
    )
    assert _say(bot, 666) == [_NOT_PAIRED]
    bot.memory.close()


def test_paired_owner_survives_restart_but_env_wins(tmp_path, no_scheduler):
    bot = TelegramFriday(_cfg(tmp_path))
    _pair(bot, 111, bot._pairing.display)
    bot.memory.close()

    restarted = TelegramFriday(_cfg(tmp_path))
    assert restarted.owner_id == 111 and restarted._pairing is None
    restarted.memory.close()

    pinned = TelegramFriday(_cfg(tmp_path, owner="5"))
    assert pinned.owner_id == 5
    assert pinned._authorized(_update(5)) and not pinned._authorized(_update(111))
    pinned.memory.close()


def test_configured_owner_wins(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path, owner="5"))
    assert bot._pairing is None and bot.pairing_banner() is None
    assert not bot._authorized(_update(6)) and bot._authorized(_update(5))
    bot.memory.close()


def test_stranger_gets_only_the_generic_reply(tmp_path, no_agent):
    unpaired = TelegramFriday(_cfg(tmp_path / "a"))
    code = unpaired._pairing.display
    for handler in (unpaired.on_message, unpaired.status, unpaired.trust_on, unpaired.start):
        upd = _update(999)
        asyncio.run(handler(upd, _ctx()))
        assert upd.effective_message.replies == [_NOT_PAIRED]
    unpaired.memory.close()
    assert code not in _NOT_PAIRED

    paired = TelegramFriday(_cfg(tmp_path / "b", owner="5"))
    for handler in (paired.on_message, paired.status, paired.trust_on, paired.start, paired.pair):
        upd = _update(999)
        asyncio.run(handler(upd, _ctx("ABCD-EFGH")))
        assert upd.effective_message.replies == ["Not authorized."]
    paired.memory.close()


def test_owner_in_group_is_ignored_and_logged(tmp_path, no_agent, caplog):
    bot = TelegramFriday(_cfg(tmp_path, owner="5"))
    assert not bot._authorized(_update(5, chat_type="group", chat_id=-100))
    with caplog.at_level(logging.WARNING, logger="friday.telegram"):
        assert _say(bot, 5, chat_type="supergroup") == []
    assert any(r.levelno == logging.WARNING and "supergroup" in r.message for r in caplog.records)
    bot.memory.close()


def test_reset_owner_clears_both_keys(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    from friday.memory import Memory

    mem = Memory(cfg.db_path)
    mem.set_setting(LEGACY_SETTING, "666")
    mem.set_setting(PAIRED_SETTING, "111")
    mem.close()
    assert telegram_bot.reset_owner_cli(cfg) == 0
    assert "Cleared" in capsys.readouterr().out
    bot = TelegramFriday(cfg)
    assert bot.owner_id is None
    assert bot.memory.get_setting(LEGACY_SETTING) is None and bot.memory.get_setting(PAIRED_SETTING) is None
    bot.memory.close()


# ── reminder delivery ───────────────────────────────────────────────────
def _capture_sends(bot):
    sent = []

    async def send_message(chat_id, text):
        sent.append((chat_id, text))

    bot._app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    return sent


def test_non_telegram_reminders_go_to_owner(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path, owner="77"))
    sent = _capture_sends(bot)
    asyncio.run(bot._deliver("cli", "hi"))
    asyncio.run(bot._deliver("tg:9", "yo"))
    assert sent == [(77, "hi"), (9, "yo")]
    bot.memory.close()


def test_delivery_without_owner_raises_for_logging(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path))
    sent = _capture_sends(bot)
    for channel in ("cli", "tg:9"):  # unpaired: nothing is delivered anywhere
        with pytest.raises(RuntimeError):
            asyncio.run(bot._deliver(channel, "hi"))
    assert sent == []
    bot.memory.close()
