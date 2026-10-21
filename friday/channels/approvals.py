"""Per-action approval requests for the Telegram bridge.

The agent runs in a worker thread (`asyncio.to_thread`) and its approver is
synchronous, while Telegram I/O lives on the bot's event loop. The broker
bridges the two:

  * `request()` (worker thread) posts the approval message on the loop with
    `run_coroutine_threadsafe`, then blocks on a Future until the owner taps a
    button, the request times out, or it is cancelled.
  * `resolve()` (loop thread, from the callback handler) checks who tapped and
    where, then resolves the request exactly once.
  * `deny_all()` releases every waiting request (on /trust_off, /reset, the
    end of a turn, or shutdown), so no worker thread is ever left blocked.

No `telegram` import here: posting and editing are injected callables.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from ..tools.base import Risk

log = logging.getLogger("friday.telegram")

# post(chat_id, approval_id, tool_name, tool_input, risk) -> (message_id, text)
# of the message carrying the buttons.
Post = Callable[[int, str, str, dict[str, Any], Risk], Awaitable[tuple[int, str]]]
# edit(chat_id, message_id, text): replace the text and remove the buttons.
Edit = Callable[[int, int, str], Awaitable[None]]

_POST_TIMEOUT = 30.0


def input_digest(tool_input: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(tool_input, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Pending:
    chat_id: int
    tool_name: str
    digest: str
    created_at: float
    # Result is (approved, reason); set exactly once, by whoever pops the entry.
    future: concurrent.futures.Future[tuple[bool, str]] = field(default_factory=concurrent.futures.Future)
    message_id: int | None = None
    text: str = ""


class ApprovalBroker:
    def __init__(
        self,
        post: Post,
        edit: Edit,
        timeout: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._post = post
        self._edit = edit
        self.timeout = timeout
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._pending: dict[str, Pending] = {}
        # The bot's running loop; captured at startup.
        self.loop: asyncio.AbstractEventLoop | None = None

    def pending_ids(self, chat_id: int | None = None) -> list[str]:
        with self._lock:
            return [k for k, p in self._pending.items() if chat_id is None or p.chat_id == chat_id]

    # ── worker thread ──────────────────────────────────────────────────
    def request(self, chat_id: int, name: str, tool_input: dict[str, Any], risk: Risk) -> tuple[bool, str]:
        """Ask the owner; block until they answer. Never raises."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return False, "the Telegram approval channel isn't running"
        approval_id = secrets.token_urlsafe(8)
        pending = Pending(chat_id, name, input_digest(tool_input), self._clock())
        with self._lock:
            self._pending[approval_id] = pending
        try:
            posted = asyncio.run_coroutine_threadsafe(
                self._post_and_record(approval_id, pending, tool_input, risk), loop
            )
            posted.result(timeout=_POST_TIMEOUT)
        except Exception:  # noqa: BLE001 — no message means no approval
            log.exception("couldn't send the approval request for %s", name)
            self._finish(approval_id, False, "", "couldn't send the approval request")
            return pending.future.result()
        try:
            return pending.future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            self._finish(approval_id, False, "Expired", f"no answer within {self.timeout:.0f}s")
            # If a tap won the race, the Future already holds its answer.
            return pending.future.result()

    async def _post_and_record(
        self, approval_id: str, pending: Pending, tool_input: dict[str, Any], risk: Risk
    ) -> None:
        # Runs on the loop, like the callback handler, so the message id is
        # recorded before any tap on its buttons can be processed.
        message_id, text = await self._post(pending.chat_id, approval_id, pending.tool_name, tool_input, risk)
        pending.message_id, pending.text = message_id, text

    # ── loop thread ────────────────────────────────────────────────────
    def resolve(
        self, approval_id: str, approve: bool, user_id: int | None, chat_id: int | None, owner_id: int | None
    ) -> str:
        """Handle a button tap. Returns the text for the callback answer."""
        if owner_id is None or user_id != owner_id:
            log.warning("Ignored an approval tap from Telegram user %s", user_id)
            return "Not authorized."
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return "Already handled."
        if pending.chat_id != chat_id:
            log.warning("Ignored an approval tap for chat %s from chat %s", pending.chat_id, chat_id)
            return "Not authorized."
        if self._clock() - pending.created_at >= self.timeout:
            self._finish(approval_id, False, "Expired", f"no answer within {self.timeout:.0f}s")
            return "Expired."
        label = "Approved" if approve else "Denied"
        reason = "approved in Telegram" if approve else "denied in Telegram"
        if not self._finish(approval_id, approve, label, reason):
            return "Already handled."
        log.info("%s %s (request %s, input sha256 %s)", label, pending.tool_name, approval_id, pending.digest[:16])
        return f"{label}."

    # ── any thread ─────────────────────────────────────────────────────
    def deny_all(self, chat_id: int | None, reason: str) -> int:
        """Deny every waiting request (for one chat, or all). Returns how many."""
        ids = self.pending_ids(chat_id)
        return sum(self._finish(i, False, f"Denied ({reason})", reason) for i in ids)

    def _finish(self, approval_id: str, approved: bool, label: str, reason: str) -> bool:
        """Resolve once. Returns False if someone else already resolved it."""
        with self._lock:
            pending = self._pending.pop(approval_id, None)
        if pending is None:
            return False
        pending.future.set_result((approved, reason))
        if label and pending.message_id is not None:
            self._schedule(self._edit(pending.chat_id, pending.message_id, f"{pending.text}\n\n→ {label}"))
        return True

    def _schedule(self, coro: Awaitable[None]) -> None:
        """Run a fire-and-forget edit on the loop, from whichever thread we're on."""
        loop = self.loop
        if loop is None or loop.is_closed():
            _close(coro)
            return

        async def guarded() -> None:
            try:
                await coro
            except Exception:  # noqa: BLE001 — a failed edit must not break anything
                log.warning("couldn't update an approval message", exc_info=True)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is loop:
                loop.create_task(guarded())
            else:
                asyncio.run_coroutine_threadsafe(guarded(), loop)
        except RuntimeError:  # loop shutting down
            _close(coro)


def _close(coro: Awaitable[None]) -> None:
    if isinstance(coro, Coroutine):
        coro.close()
