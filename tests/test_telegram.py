"""Network-free checks for the Telegram bridge's auth and delivery logic."""
import asyncio
from types import SimpleNamespace

import pytest

from friday.channels.telegram_bot import TelegramFriday
from friday.config import Config


def _cfg(tmp_path, owner=""):
    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.telegram_owner_id = owner
    cfg.db_path = tmp_path / "db.sqlite"
    cfg.workspace = tmp_path / "ws"
    return cfg


def _update(uid):
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid))


def test_owner_claim_is_persisted_across_restarts(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path))
    assert bot._authorized(_update(111))
    assert not bot._authorized(_update(222))
    bot.memory.close()

    restarted = TelegramFriday(_cfg(tmp_path))
    assert restarted.owner_id == 111
    assert not restarted._authorized(_update(222))  # a stranger can't claim after restart
    restarted.memory.close()


def test_configured_owner_wins(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path, owner="5"))
    assert not bot._authorized(_update(6)) and bot._authorized(_update(5))
    bot.memory.close()


def test_non_telegram_reminders_go_to_owner(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path, owner="77"))
    sent = []

    async def send_message(chat_id, text):
        sent.append((chat_id, text))

    bot._app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    asyncio.run(bot._deliver("cli", "hi"))
    asyncio.run(bot._deliver("tg:9", "yo"))
    assert sent == [(77, "hi"), (9, "yo")]
    bot.memory.close()


def test_delivery_without_owner_raises_for_logging(tmp_path):
    bot = TelegramFriday(_cfg(tmp_path))
    bot._app = SimpleNamespace(bot=SimpleNamespace(send_message=None))
    with pytest.raises(RuntimeError):
        asyncio.run(bot._deliver("cli", "hi"))
    bot.memory.close()
