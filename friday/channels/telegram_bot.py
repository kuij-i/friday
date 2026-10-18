"""Telegram bridge — talk to Friday from your iPhone.

Safety model (no interactive approval round-trips over chat):
  * Only TELEGRAM_OWNER_ID may talk to Friday. If unset, the first user to
    message the bot claims ownership; the claim is persisted in the memory DB
    (so a restart doesn't reopen it) and the id is logged for you to pin into
    your .env.
  * Side-effecting tools (write/shell/python/memory-write) only run when the
    chat is in "trust" mode. Toggle with /trust_on and /trust_off. Default off,
    so from your phone Friday answers and inspects freely but won't change
    anything on your machine until you say so.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from telegram import Bot, Update
from telegram.constants import ChatAction
from telegram.error import InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..agent import Agent
from ..config import Config
from ..memory import Memory
from ..scheduler import Scheduler
from ..tools.base import Risk
from ..transcribe import Transcriber

log = logging.getLogger("friday.telegram")

_OWNER_SETTING = "telegram_owner_id"
_COMMANDS = [
    ("help", "show this list"),
    ("trust_on", "allow me to write files and run commands"),
    ("trust_off", "read-only mode (default)"),
    ("status", "model, effort and trust state"),
    ("memory", "what I remember"),
    ("reminders", "list scheduled reminders"),
    ("reset", "clear this chat's history"),
]
_TG_LIMIT = 4000  # Telegram caps messages at 4096 chars; leave headroom


def _chunks(text: str) -> list[str]:
    text = text or "(no reply)"
    return [text[i : i + _TG_LIMIT] for i in range(0, len(text), _TG_LIMIT)]


class TelegramFriday:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.memory = Memory(config.db_path)
        self.owner_id: int | None = None
        if config.telegram_owner_id:
            self.owner_id = int(config.telegram_owner_id)
        else:
            claimed = self.memory.get_setting(_OWNER_SETTING)
            if claimed and claimed.lstrip("-").isdigit():
                self.owner_id = int(claimed)
                log.info("Using previously claimed owner id %s", claimed)
        self._agents: dict[int, Agent] = {}
        self._trust: dict[int, bool] = {}
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._transcriber = Transcriber(config.whisper_model)
        self._scheduler: Scheduler | None = None
        self._app: Application | None = None

    # ── authorization ──────────────────────────────────────────────────
    def _authorized(self, update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else None
        if uid is None:
            return False
        if self.owner_id is None:
            self.owner_id = uid
            try:
                self.memory.set_setting(_OWNER_SETTING, str(uid))
            except Exception:  # noqa: BLE001 — still locked for this process
                log.exception("failed to persist owner claim")
            log.warning("Ownership claimed by Telegram user id %s — pin TELEGRAM_OWNER_ID=%s in .env", uid, uid)
            return True
        if uid != self.owner_id:
            log.warning("Rejected Telegram user id %s", uid)
            return False
        return True

    @staticmethod
    async def _reply(update: Update, text: str) -> None:
        """Reply to whatever message triggered the update (edited ones too)."""
        msg = update.effective_message
        if msg is None:
            return
        for chunk in _chunks(text):
            try:
                await msg.reply_text(chunk)
            except Exception:  # noqa: BLE001
                log.exception("failed to send reply chunk")

    def _agent_for(self, chat_id: int) -> Agent:
        if chat_id not in self._agents:
            def approver(name: str, tool_input: dict[str, Any], risk: Risk) -> tuple[bool, str]:
                if self.config.auto_approve or self._trust.get(chat_id, False):
                    return True, "trust mode"
                return False, "trust mode is off — send /trust_on to allow actions"

            self._agents[chat_id] = Agent(
                self.config,
                self.memory,
                channel=f"tg:{chat_id}",
                approver=approver,
            )
        return self._agents[chat_id]

    # ── handlers ───────────────────────────────────────────────────────
    async def start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await self._reply(update, "Not authorized.")
            return
        lines = "\n".join(f"/{name} — {desc}" for name, desc in _COMMANDS)
        await self._reply(
            update,
            "Friday here. I can answer, inspect your machine, and help you code.\n"
            "I won't change anything until you turn on trust.\n\n" + lines,
        )

    async def trust_on(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        self._trust[update.effective_chat.id] = True
        await self._reply(update, "Trust ON — I can now write files and run commands. Be careful.")

    async def trust_off(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        self._trust[update.effective_chat.id] = False
        await self._reply(update, "Trust OFF — read-only mode.")

    async def status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        trust = self._trust.get(update.effective_chat.id, False)
        await self._reply(
            update,
            f"model: {self.config.model}\neffort: {self.config.effort}\ntrust: {'ON' if trust else 'off'}"
        )

    async def memory_cmd(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        blob = self.memory.context_blob()
        await self._reply(update, blob[:3500] or "(nothing remembered yet)")

    async def reset(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        n = self.memory.clear_channel(f"tg:{update.effective_chat.id}")
        await self._reply(update, f"Cleared {n} messages.")

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    @staticmethod
    async def _keep_typing(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        """Telegram's typing indicator expires after ~5s; refresh it during long turns."""
        while True:
            with contextlib.suppress(Exception):
                await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    async def _respond(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        if update.effective_chat is None:
            return
        chat_id = update.effective_chat.id
        # One turn at a time per chat, so rapid messages don't interleave.
        async with self._lock_for(chat_id):
            typing = asyncio.create_task(self._keep_typing(ctx, chat_id))
            try:
                agent = self._agent_for(chat_id)
                reply = await asyncio.to_thread(agent.send, text)
            except Exception as e:  # noqa: BLE001 — agent.send already guards, belt-and-braces
                log.exception("agent error")
                reply = f"Something went wrong: {e}"
            finally:
                typing.cancel()
        await self._reply(update, reply or "(no reply)")

    async def _deliver(self, channel: str, text: str) -> None:
        """Scheduler delivery: channel is 'tg:<chat_id>'.

        Reminders created elsewhere (e.g. the CLI) go to the owner's private
        chat instead of being silently dropped.
        """
        chat_id: int | None = None
        if channel.startswith("tg:"):
            try:
                chat_id = int(channel.split(":", 1)[1])
            except (ValueError, IndexError):
                chat_id = None
        if chat_id is None:
            chat_id = self.owner_id
        if chat_id is None or self._app is None:
            raise RuntimeError(f"no Telegram chat to deliver reminder for channel {channel!r}")
        for chunk in _chunks(text):
            await self._app.bot.send_message(chat_id=chat_id, text=chunk)

    async def on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await self._reply(update, "Not authorized.")
            return
        msg = update.effective_message
        await self._respond(update, ctx, (msg.text if msg else "") or "")

    async def on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await self._reply(update, "Not authorized.")
            return
        if not self._transcriber.available():
            await self._reply(
                update,
                "Voice needs local transcription. Install it with:\n"
                "pip install -r requirements-voice.txt"
            )
            return
        msg = update.effective_message
        voice = (msg.voice or msg.audio) if msg else None
        if voice is None:
            return
        with contextlib.suppress(Exception):
            await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            tg_file = await ctx.bot.get_file(voice.file_id)
            with tempfile.TemporaryDirectory() as tmp:
                audio_path = Path(tmp) / "note.ogg"
                await tg_file.download_to_drive(str(audio_path))
                text = await asyncio.to_thread(self._transcriber.transcribe, audio_path)
        except Exception as e:  # noqa: BLE001
            log.exception("transcription error")
            await self._reply(update, f"Couldn't transcribe that: {e}")
            return
        if not text:
            await self._reply(update, "I couldn't make out any speech in that note.")
            return
        await self._reply(update, f"🎙 heard: {text}")
        await self._respond(update, ctx, text)

    async def _on_startup(self, app: Application) -> None:
        self._scheduler = Scheduler(self.memory, self._deliver)
        self._scheduler.start()
        try:  # populate the "/" menu in the Telegram app; cosmetic, so never fatal
            from telegram import BotCommand

            await app.bot.set_my_commands([BotCommand(n, d) for n, d in _COMMANDS])
        except Exception:  # noqa: BLE001
            log.warning("couldn't register the bot command menu", exc_info=True)

    async def _on_shutdown(self, _app: Application) -> None:
        if self._scheduler:
            await self._scheduler.stop()
        self.memory.close()

    def build(self) -> Application:
        app = (
            Application.builder()
            .token(self.config.telegram_token)
            .post_init(self._on_startup)
            .post_shutdown(self._on_shutdown)
            .build()
        )
        self._app = app
        app.add_handler(CommandHandler(["start", "help"], self.start))
        app.add_handler(CommandHandler("trust_on", self.trust_on))
        app.add_handler(CommandHandler("trust_off", self.trust_off))
        app.add_handler(CommandHandler("status", self.status))
        app.add_handler(CommandHandler("memory", self.memory_cmd))
        app.add_handler(CommandHandler("reset", self.reset))
        app.add_handler(CommandHandler("reminders", self.reminders_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        app.add_error_handler(self._on_error)
        return app

    async def _on_error(self, _update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("telegram handler error", exc_info=ctx.error)

    async def reminders_cmd(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        items = self.memory.list_reminders(f"tg:{update.effective_chat.id}")
        if not items:
            await self._reply(update, "No active reminders.")
            return
        lines = []
        for r in items:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["next_run"]))
            if r.get("daily_time"):
                kind = f"daily {r['daily_time']}"
            else:
                kind = f"every {r['interval'] / 60:.0f}m" if r["interval"] else "once"
            lines.append(f"#{r['id']}  {when}  ({kind})  {r['text']}")
        await self._reply(update, "\n".join(lines)[:3900])


def run_telegram(config: Config) -> int:
    problems = config.validate()
    if not config.telegram_token:
        problems.append("TELEGRAM_BOT_TOKEN is not set — create a bot with @BotFather.")
    if problems:
        for p in problems:
            print(f"config error: {p}", file=sys.stderr)
        print("Run `python run.py doctor` after fixing your .env.", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # The HTTP client logs every long-poll request at INFO, which drowns real events.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config.ensure_dirs()
        bot = TelegramFriday(config)
        app = bot.build()
    except Exception as e:  # noqa: BLE001 — unwritable dir, corrupt DB, malformed token
        print(f"startup error: {e}", file=sys.stderr)
        return 1
    try:
        username = preflight(config.telegram_token)
    except InvalidToken:
        print("Telegram rejected TELEGRAM_BOT_TOKEN — copy it again from @BotFather.", file=sys.stderr)
        bot.memory.close()
        return 1
    except NetworkError as e:
        print(f"Couldn't reach Telegram ({e}). Check your internet connection and retry.", file=sys.stderr)
        bot.memory.close()
        return 1
    print(f"Friday is running as @{username}. Message it on Telegram. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


def preflight(token: str) -> str:
    """Validate the token, then leave a fresh event loop for run_polling.

    asyncio.run() closes its loop and unsets the current one; older
    python-telegram-bot releases call asyncio.get_event_loop() in run_polling,
    which then raises "There is no current event loop" on Python 3.12+.
    """
    try:
        return asyncio.run(_check_token(token))
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


async def _check_token(token: str) -> str:
    """Fail fast with a clear message on a bad token, before PTB's noisy retry loop."""
    async with Bot(token) as tg:
        me = await tg.get_me()
    return me.username or str(me.id)
