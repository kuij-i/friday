"""Telegram bridge — talk to Friday from your iPhone.

Safety model (no interactive approval round-trips over chat):
  * Only the owner may talk to Friday, and only in a private chat; group and
    channel updates are ignored (and logged), even from the owner.
  * The owner is TELEGRAM_OWNER_ID from .env, or someone who sent /pair with
    the one-time code printed on the local terminal (see pairing.py). There
    are no implicit claims: until paired, the bot only answers "not paired".
  * Every side-effecting tool needs a per-action tap on ✅ Approve / ❌ Deny.
    The request shows the full tool input as plain text and is denied if
    unanswered within FRIDAY_TELEGRAM_APPROVAL_TIMEOUT (see approvals.py).
  * /trust_on [minutes] auto-approves only Risk.LOW tools (files, memory,
    reminders) until it expires. DANGEROUS tools (shell, Python, self-coding)
    always need a tap. Trust lives in memory only, so a restart clears it.
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

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..agent import Agent
from ..config import TRUST_MINUTES_BOUNDS, Config
from ..memory import Memory
from ..scheduler import Scheduler
from ..tools.base import Risk, approval_preview
from ..transcribe import Transcriber
from .approvals import ApprovalBroker
from .pairing import (
    PAIRED_SETTING,
    PairingCode,
    PairResult,
    legacy_claim_message,
    reset_owner,
    resolve_owner,
)

log = logging.getLogger("friday.telegram")

_NOT_PAIRED = (
    "This bot isn't paired. Send /pair <code> from the code shown on the computer running Friday."
)
_COMMANDS = [
    ("help", "show this list"),
    ("trust_on", "auto-approve small actions (files, memory, reminders) for N min"),
    ("trust_off", "ask before every action (default)"),
    ("status", "model, effort and trust state"),
    ("memory", "what I remember"),
    ("reminders", "list scheduled reminders"),
    ("reset", "clear this chat's history"),
    ("pair", "pair with the code shown on the computer"),
]
_TG_LIMIT = 4000  # Telegram caps messages at 4096 chars; leave headroom


def _chunks(text: str, limit: int = _TG_LIMIT) -> list[str]:
    text = text or "(no reply)"
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class TelegramFriday:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.memory = Memory(config.db_path)
        status = resolve_owner(config, self.memory)
        self.owner_id: int | None = status.owner_id
        self._pairing: PairingCode | None = None
        if self.owner_id is None:
            # Pairing mode: nobody is served until /pair succeeds.
            if status.legacy_claim:
                log.warning(legacy_claim_message(status.legacy_claim))
            self._pairing = PairingCode()
        self._agents: dict[int, Agent] = {}
        # chat_id -> time.monotonic() deadline; in memory only, never persisted.
        self._trust_until: dict[int, float] = {}
        self._approvals = ApprovalBroker(
            self._post_approval, self._edit_approval, timeout=config.effective_approval_timeout
        )
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._transcriber = Transcriber(config.whisper_model)
        self._scheduler: Scheduler | None = None
        self._app: Application | None = None

    # ── authorization ──────────────────────────────────────────────────
    def pairing_banner(self) -> str | None:
        """Text for the local terminal only — never sent over Telegram or logged."""
        if self._pairing is None:
            return None
        return (
            f"Pairing code: {self._pairing.display} (valid 10 minutes, single use).\n"
            f"In a private chat with the bot, send:  /pair {self._pairing.display}"
        )

    def _authorized(self, update: Update) -> bool:
        """True only for the owner, in a private chat."""
        uid = update.effective_user.id if update.effective_user else None
        chat = update.effective_chat
        return (
            self.owner_id is not None
            and uid == self.owner_id
            and chat is not None
            and chat.type == "private"
        )

    async def _gate(self, update: Update) -> bool:
        """Authorize an update, replying to rejected private chats.

        Replies never reveal the owner id, the pairing code or config.
        """
        uid = update.effective_user.id if update.effective_user else None
        chat = update.effective_chat
        if uid is None or chat is None:
            return False
        if chat.type != "private":
            log.warning("Ignored an update from user %s in a %s chat; only private chats are served", uid, chat.type)
            return False
        if self.owner_id is None:
            await self._reply(update, _NOT_PAIRED)
            return False
        if uid != self.owner_id:
            log.warning("Rejected Telegram user id %s", uid)
            await self._reply(update, "Not authorized.")
            return False
        return True

    async def pair(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else None
        chat = update.effective_chat
        if uid is None or chat is None:
            return
        if chat.type != "private":
            log.warning("Ignored /pair from user %s in a %s chat", uid, chat.type)
            return
        if self.owner_id is not None:
            await self._reply(update, "Already paired." if uid == self.owner_id else "Not authorized.")
            return
        attempt = " ".join(getattr(ctx, "args", None) or [])
        if not attempt:
            await self._reply(update, "Usage: /pair <code> (the code is shown on the computer running Friday).")
            return
        result = self._pairing.check(attempt) if self._pairing else PairResult.LOCKED
        if result is PairResult.OK:
            self.owner_id = uid
            self._pairing = None
            try:
                self.memory.set_setting(PAIRED_SETTING, str(uid))
            except Exception:  # noqa: BLE001 — still paired for this process
                log.exception("failed to persist the paired owner")
            print(f"Paired with Telegram user {uid}. Pin TELEGRAM_OWNER_ID={uid} in .env", flush=True)
            self._start_scheduler()
            await self._reply(update, "Paired — you're the owner of this Friday now. Send /help to begin.")
            return
        if result is PairResult.WRONG:
            log.warning("Wrong pairing code from Telegram user %s", uid)
        if result in (PairResult.WRONG, PairResult.EXPIRED):
            await self._reply(update, "That code is wrong or has expired.")
        else:
            await self._reply(update, "Pairing is closed. Restart Friday on the computer to get a new code.")

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

    # ── approvals & trust ──────────────────────────────────────────────
    def _trust_left(self, chat_id: int) -> float:
        """Seconds of trust left for this chat (0 when off or expired)."""
        return max(0.0, self._trust_until.get(chat_id, 0.0) - time.monotonic())

    async def _post_approval(
        self, chat_id: int, approval_id: str, name: str, tool_input: dict[str, Any], risk: Risk
    ) -> tuple[int, str]:
        """Send the request as plain text (no parse_mode, so model output can't
        hide or restyle anything). Long input is split; buttons go on the last part."""
        if self._app is None:
            raise RuntimeError("Telegram app isn't running")
        text = f"Approval needed — {name} ({risk.name})\n\n{approval_preview(tool_input, risk)}"
        parts = _chunks(text, _TG_LIMIT - 20)
        if len(parts) > 1:
            parts = [f"(part {i}/{len(parts)})\n{p}" for i, p in enumerate(parts, 1)]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"ap:{approval_id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"dn:{approval_id}"),
        ]])
        message = None
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            message = await self._app.bot.send_message(
                chat_id=chat_id, text=part, reply_markup=keyboard if last else None
            )
        assert message is not None  # parts is never empty
        return message.message_id, parts[-1]

    async def _edit_approval(self, chat_id: int, message_id: int, text: str) -> None:
        if self._app is not None:
            await self._app.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=None
            )

    async def on_callback(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        kind, _, approval_id = (query.data or "").partition(":")
        user_id = query.from_user.id if query.from_user else None
        chat = query.message.chat if query.message is not None else None
        if kind not in {"ap", "dn"}:
            answer = "Unknown action."
        elif chat is None or chat.type != "private":
            log.warning("Ignored an approval tap from user %s outside a private chat", user_id)
            answer = "Not authorized."
        else:
            answer = self._approvals.resolve(approval_id, kind == "ap", user_id, chat.id, self.owner_id)
        with contextlib.suppress(Exception):
            await query.answer(answer)

    def _agent_for(self, chat_id: int) -> Agent:
        if chat_id not in self._agents:
            def approver(name: str, tool_input: dict[str, Any], risk: Risk) -> tuple[bool, str]:
                # FRIDAY_AUTO_APPROVE is handled by the agent before this is called.
                if risk == Risk.LOW and self._trust_left(chat_id) > 0:
                    return True, "trust mode"
                # LOW without trust, and DANGEROUS always: ask with buttons.
                return self._approvals.request(chat_id, name, tool_input, risk)

            self._agents[chat_id] = Agent(
                self.config,
                self.memory,
                channel=f"tg:{chat_id}",
                approver=approver,
            )
        return self._agents[chat_id]

    # ── handlers ───────────────────────────────────────────────────────
    async def start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        lines = "\n".join(f"/{name} — {desc}" for name, desc in _COMMANDS)
        await self._reply(
            update,
            "Friday here. I can answer, inspect your machine, and help you code.\n"
            "I ask before every action with ✅/❌ buttons. Shell, Python and self-coding "
            "always ask, even in trust mode.\n\n" + lines,
        )

    async def trust_on(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        lo, hi = TRUST_MINUTES_BOUNDS
        args = getattr(ctx, "args", None) or []
        minutes = self.config.effective_trust_minutes
        if args:
            try:
                minutes = int(args[0])
            except ValueError:
                await self._reply(update, f"Usage: /trust_on [minutes]  ({lo}–{hi}, default {minutes})")
                return
        minutes = max(lo, min(hi, minutes))
        self._trust_until[update.effective_chat.id] = time.monotonic() + minutes * 60
        await self._reply(
            update,
            f"Trust ON for {minutes} min — file writes, memory and reminder changes run without asking. "
            "Shell, Python and self-coding still ask every time.",
        )

    async def trust_off(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        chat_id = update.effective_chat.id
        self._trust_until.pop(chat_id, None)
        cancelled = self._approvals.deny_all(chat_id, "trust turned off")
        note = f" Cancelled {cancelled} waiting request(s)." if cancelled else ""
        await self._reply(update, "Trust OFF — I'll ask before every action." + note)

    async def status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        left = self._trust_left(update.effective_chat.id)
        trust = f"ON for LOW actions — {max(1, round(left / 60))} min left" if left > 0 else "off"
        if self.config.auto_approve:
            dangerous = "⚠ AUTO-APPROVE IS ON — nothing asks (FRIDAY_AUTO_APPROVE)"
        else:
            dangerous = "always ask"
        await self._reply(
            update,
            f"provider: {self.config.provider}\nmodel: {self.config.model}\n"
            f"effort: {self.config.effort}\ntrust: {trust}\ndangerous actions: {dangerous}\n"
            f"approval timeout: {self._approvals.timeout:.0f}s",
        )

    async def memory_cmd(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        blob = self.memory.context_blob()
        await self._reply(update, blob[:3500] or "(nothing remembered yet)")

    async def reset(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        self._approvals.deny_all(update.effective_chat.id, "chat reset")
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
                # Safety net: nothing from this turn may stay waiting.
                self._approvals.deny_all(chat_id, "turn ended")
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
        if self.owner_id is None:
            raise RuntimeError("Telegram bot isn't paired; not delivering reminders")
        if chat_id is None:
            chat_id = self.owner_id
        if self._app is None:
            raise RuntimeError(f"no Telegram chat to deliver reminder for channel {channel!r}")
        for chunk in _chunks(text):
            await self._app.bot.send_message(chat_id=chat_id, text=chunk)

    async def on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        msg = update.effective_message
        await self._respond(update, ctx, (msg.text if msg else "") or "")

    async def on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
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

    def _start_scheduler(self) -> None:
        # Only with an owner: a failed delivery still advances the reminder,
        # so running unpaired would silently drop one-shot reminders.
        if self._scheduler is None and self.owner_id is not None:
            self._scheduler = Scheduler(self.memory, self._deliver)
            self._scheduler.start()

    async def _on_startup(self, app: Application) -> None:
        self._approvals.loop = asyncio.get_running_loop()
        self._start_scheduler()
        try:  # populate the "/" menu in the Telegram app; cosmetic, so never fatal
            from telegram import BotCommand

            await app.bot.set_my_commands([BotCommand(n, d) for n, d in _COMMANDS])
        except Exception:  # noqa: BLE001
            log.warning("couldn't register the bot command menu", exc_info=True)

    async def _on_shutdown(self, _app: Application) -> None:
        self._approvals.deny_all(None, "Friday is shutting down")
        if self._scheduler:
            await self._scheduler.stop()
        self.memory.close()

    def build(self) -> Application:
        app = (
            Application.builder()
            .token(self.config.telegram_token)
            # Updates must be handled while a turn waits on an approval tap
            # (or /trust_off); per-chat locks still serialise normal messages.
            .concurrent_updates(True)
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
        app.add_handler(CommandHandler("pair", self.pair))
        app.add_handler(CallbackQueryHandler(self.on_callback, pattern=r"^(ap|dn):"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        app.add_error_handler(self._on_error)
        return app

    async def _on_error(self, _update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("telegram handler error", exc_info=ctx.error)

    async def reminders_cmd(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
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
    banner = bot.pairing_banner()
    if banner:
        print("\nNo Telegram owner is set, so the bot is in PAIRING MODE.\n" + banner, flush=True)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


def reset_owner_cli(config: Config) -> int:
    """`python run.py telegram --reset-owner`: forget any stored (paired or legacy) owner."""
    if not config.db_path.exists():
        print("No database yet; there is no stored Telegram owner.")
        return 0
    try:
        memory = Memory(config.db_path)
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't open the database {config.db_path}: {e}", file=sys.stderr)
        return 1
    try:
        removed = reset_owner(memory)
    finally:
        memory.close()
    if removed:
        print("Cleared the stored Telegram owner. Next start will ask you to pair.")
    else:
        print("No stored Telegram owner to clear.")
    if config.telegram_owner_id:
        print(f"Note: TELEGRAM_OWNER_ID={config.telegram_owner_id} in .env still sets the owner.")
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
