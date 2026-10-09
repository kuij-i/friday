"""Reminder delivery loop.

Runs as a background asyncio task inside the Telegram bridge. Every tick it
asks memory for due reminders and delivers each via a supplied async callback,
then reschedules recurring ones / closes one-offs. It is deliberately simple
and defensive: a delivery that raises is logged and the reminder is still
advanced, so one bad send can't wedge the whole loop or spam on every tick.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .memory import Memory

log = logging.getLogger("friday.scheduler")

# Delivery callback: (channel, text) -> awaitable. `channel` is like "tg:<id>".
Deliver = Callable[[str, str], Awaitable[None]]


class Scheduler:
    def __init__(self, memory: Memory, deliver: Deliver, tick_seconds: float = 20.0):
        self.memory = memory
        self.deliver = deliver
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        log.info("scheduler started (tick=%ss)", self.tick_seconds)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 — never let the loop die
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except TimeoutError:
                pass

    async def _tick(self) -> None:
        now = time.time()
        for r in self.memory.due_reminders(now):
            fired = time.time()
            try:
                await self.deliver(r["channel"], f"⏰ Reminder: {r['text']}")
            except Exception:  # noqa: BLE001 — advance anyway so we don't loop-spam
                log.exception("failed to deliver reminder #%s", r.get("id"))
            # Advance regardless of delivery success to avoid repeated retries.
            self.memory.reschedule_or_close(r["id"], r.get("interval"), fired)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
