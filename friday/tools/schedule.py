"""Scheduling tools: let Friday set reminders and recurring nudges.

Reminders are stored in the shared memory DB (table `reminders`) and delivered
by friday.scheduler, which runs inside the Telegram bridge. A reminder is bound
to the channel that created it, so a reminder you set from your phone fires back
to your phone.
"""
from __future__ import annotations

import math
import time
from datetime import datetime

from ..memory import Memory, next_daily_run
from .base import Risk, Tool, ToolResult

_DAY = 86_400.0
_MAX_MINUTES = 10 * 365 * 24 * 60  # ten years; beyond that it's almost surely a mistake


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _minutes(value, name: str) -> float:
    """Parse a positive, finite, sane number of minutes or raise ValueError."""
    try:
        m = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not math.isfinite(m) or m <= 0:
        raise ValueError(f"{name} must be a positive number.")
    if m > _MAX_MINUTES:
        raise ValueError(f"{name} is too large (max ten years).")
    return m


def make_schedule_tools(memory: Memory, channel: str) -> list[Tool]:
    def schedule_reminder(
        text: str,
        delay_minutes: float | None = None,
        daily_time: str | None = None,
        repeat_minutes: float | None = None,
    ) -> ToolResult:
        text = str(text or "").strip()
        if not text:
            return ToolResult("A reminder needs some text.", is_error=True)

        now = time.time()
        interval: float | None = None
        daily: str | None = None

        try:
            if daily_time:
                try:
                    hh, mm = (int(x) for x in str(daily_time).strip().split(":"))
                    if not (0 <= hh < 24 and 0 <= mm < 60):
                        raise ValueError
                except Exception:
                    return ToolResult("daily_time must be 'HH:MM' (24h).", is_error=True)
                daily = f"{hh:02d}:{mm:02d}"
                next_run = next_daily_run(daily, now)
                interval = _DAY
            elif delay_minutes is not None:
                next_run = now + _minutes(delay_minutes, "delay_minutes") * 60
                if repeat_minutes:
                    interval = max(60.0, _minutes(repeat_minutes, "repeat_minutes") * 60)
            elif repeat_minutes:
                interval = max(60.0, _minutes(repeat_minutes, "repeat_minutes") * 60)
                next_run = now + interval
            else:
                return ToolResult(
                    "Specify when: delay_minutes, daily_time='HH:MM', or repeat_minutes.",
                    is_error=True,
                )
        except ValueError as e:
            return ToolResult(str(e), is_error=True)

        rid = memory.add_reminder(channel, text, next_run, interval, daily_time=daily)
        if daily:
            kind = f"daily at {daily}"
        else:
            kind = f"every {interval / 60:.0f} min" if interval else "once"
        note = ""
        if not channel.startswith("tg:"):
            note = "\n(Delivered via the Telegram bot to its owner — the bot must be running.)"
        return ToolResult(f"Reminder #{rid} set for {_fmt(next_run)} ({kind}): {text}{note}")

    def list_reminders() -> ToolResult:
        items = memory.list_reminders(channel)
        if not items:
            return ToolResult("No active reminders.")
        lines = []
        for r in items:
            if r.get("daily_time"):
                kind = f"daily {r['daily_time']}"
            else:
                kind = f"every {r['interval'] / 60:.0f}m" if r["interval"] else "once"
            lines.append(f"#{r['id']}  {_fmt(r['next_run'])}  ({kind})  {r['text']}")
        return ToolResult("\n".join(lines))

    def cancel_reminder(reminder_id: int) -> ToolResult:
        try:
            rid = int(reminder_id)
        except (TypeError, ValueError):
            return ToolResult("reminder_id must be a number.", is_error=True)
        ok = memory.cancel_reminder(rid, channel)
        return ToolResult(f"Cancelled reminder #{rid}." if ok else f"No active reminder #{rid}.")

    return [
        Tool(
            name="schedule_reminder",
            description=(
                "Schedule a reminder or recurring nudge that Friday will send later. "
                "Use delay_minutes for a one-off, daily_time='HH:MM' for a daily reminder, "
                "or repeat_minutes for a recurring one."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to remind about / do."},
                    "delay_minutes": {"type": "number", "description": "Fire once, this many minutes from now."},
                    "daily_time": {"type": "string", "description": "Fire daily at this 24h time, 'HH:MM'."},
                    "repeat_minutes": {"type": "number", "description": "Repeat every N minutes."},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            risk=Risk.LOW,
            handler=schedule_reminder,
        ),
        Tool(
            name="list_reminders",
            description="List this channel's active reminders.",
            input_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            risk=Risk.SAFE,
            handler=list_reminders,
        ),
        Tool(
            name="cancel_reminder",
            description="Cancel an active reminder by its id.",
            input_schema={
                "type": "object",
                "properties": {"reminder_id": {"type": "integer"}},
                "required": ["reminder_id"],
                "additionalProperties": False,
            },
            risk=Risk.LOW,
            handler=cancel_reminder,
        ),
    ]
