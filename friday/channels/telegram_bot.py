"""Telegram bridge — talk to Friday from your iPhone.

Safety model (no interactive approval round-trips over chat):
  * Only TELEGRAM_OWNER_ID may talk to Friday. If unset, the first user to
    message the bot claims ownership and the id is printed for you to pin
    into your .env.
  * Side-effecting tools (write/shell/python/memory-write) only run when the
    chat is in "trust" mode. Toggle with /trust_on and /trust_off. Default off,
    so from your phone Friday answers and inspects freely but won't change
    anything on your machine until you say so.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
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
from ..tools.base import Risk
from ..transcribe import Transcriber

log = logging.getLogger("friday.telegram")


class TelegramFriday:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.memory = Memory(config.db_path)
        self.owner_id: int | None = int(config.telegram_owner_id) if config.telegram_owner_id else None
        self._agents: dict[int, Agent] = {}
        self._trust: dict[int, bool] = {}
        self._transcriber = Transcriber(config.whisper_model)

    # ── authorization ──────────────────────────────────────────────────
    def _authorized(self, update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else None
        if uid is None:
            return False
        if self.owner_id is None:
            self.owner_id = uid
            log.warning("Ownership claimed by Telegram user id %s — pin TELEGRAM_OWNER_ID=%s in .env", uid, uid)
            return True
        return uid == self.owner_id

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
            await update.message.reply_text("Not authorized.")
            return
        await update.message.reply_text(
            "Friday here. I can answer, inspect your machine, and help you code.\n\n"
            "I won't change anything until you turn on trust:\n"
            "/trust_on — allow me to write files and run commands\n"
            "/trust_off — read-only mode (default)\n"
            "/status — show trust state\n"
            "/memory — what I remember\n"
            "/reset — clear this chat's history"
        )

    async def trust_on(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        self._trust[update.effective_chat.id] = True
        await update.message.reply_text("Trust ON — I can now write files and run commands. Be careful.")

    async def trust_off(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        self._trust[update.effective_chat.id] = False
        await update.message.reply_text("Trust OFF — read-only mode.")

    async def status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        trust = self._trust.get(update.effective_chat.id, False)
        await update.message.reply_text(
            f"model: {self.config.model}\neffort: {self.config.effort}\ntrust: {'ON' if trust else 'off'}"
        )

    async def memory_cmd(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        blob = self.memory.context_blob()
        await update.message.reply_text(blob[:3500] or "(nothing remembered yet)")

    async def reset(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        n = self.memory.clear_channel(f"tg:{update.effective_chat.id}")
        await update.message.reply_text(f"Cleared {n} messages.")

    async def _respond(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        chat_id = update.effective_chat.id
        await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        agent = self._agent_for(chat_id)
        try:
            reply = await asyncio.to_thread(agent.send, text)
        except Exception as e:  # noqa: BLE001
            log.exception("agent error")
            reply = f"Something went wrong: {e}"
        # Telegram messages cap at 4096 chars.
        for i in range(0, len(reply) or 1, 4000):
            await update.message.reply_text(reply[i : i + 4000] or "(no reply)")

    async def on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        await self._respond(update, ctx, update.message.text or "")

    async def on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        if not self._transcriber.available():
            await update.message.reply_text(
                "Voice needs local transcription. Install it with:\n"
                "pip install -r requirements-voice.txt"
            )
            return
        voice = update.message.voice or update.message.audio
        await ctx.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
        try:
            tg_file = await ctx.bot.get_file(voice.file_id)
            with tempfile.TemporaryDirectory() as tmp:
                audio_path = Path(tmp) / "note.ogg"
                await tg_file.download_to_drive(str(audio_path))
                text = await asyncio.to_thread(self._transcriber.transcribe, audio_path)
        except Exception as e:  # noqa: BLE001
            log.exception("transcription error")
            await update.message.reply_text(f"Couldn't transcribe that: {e}")
            return
        if not text:
            await update.message.reply_text("I couldn't make out any speech in that note.")
            return
        await update.message.reply_text(f"🎙 heard: {text}")
        await self._respond(update, ctx, text)

    def build(self) -> Application:
        app = Application.builder().token(self.config.telegram_token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("trust_on", self.trust_on))
        app.add_handler(CommandHandler("trust_off", self.trust_off))
        app.add_handler(CommandHandler("status", self.status))
        app.add_handler(CommandHandler("memory", self.memory_cmd))
        app.add_handler(CommandHandler("reset", self.reset))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        return app


def run_telegram(config: Config) -> None:
    config.ensure_dirs()
    problems = config.validate()
    if not config.telegram_token:
        problems.append("TELEGRAM_BOT_TOKEN is not set — create a bot with @BotFather.")
    if problems:
        for p in problems:
            print(f"config error: {p}")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    bot = TelegramFriday(config)
    app = bot.build()
    print("Friday Telegram bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
