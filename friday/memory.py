"""Persistent memory for Friday.

Stores, all in one SQLite file:
  * facts        — durable things Friday learns ("my main project is X").
  * preferences  — key/value settings about how you like to work.
  * messages     — rolling conversation log per channel, for continuity.
  * reminders    — scheduled one-off / recurring prompts (used by the scheduler).

This is the heart of phase-1 self-improvement: Friday gets more useful over
time because it remembers you, not because it rewrites its own code.

Design notes for robustness:
  * One connection guarded by a re-entrant lock, so the sync agent, the CLI,
    and the async Telegram scheduler can all share one Memory safely.
  * WAL mode + busy_timeout so concurrent readers/writers don't error out.
  * Hard caps on stored text so a runaway tool result can't bloat the DB.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Caps keep a single pathological input from ballooning the database.
_MAX_FACT = 8_000
_MAX_PREF = 8_000
_MAX_MESSAGE = 200_000
# Budget for the memory summary injected into every system prompt. Without it,
# enough stored facts would overflow the context window and wedge every turn —
# including the turn the user would use to ask Friday to forget things.
_MAX_CONTEXT_BLOB = 24_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic     TEXT NOT NULL,
    content   TEXT NOT NULL,
    created   REAL NOT NULL,
    UNIQUE(topic, content)
);
CREATE TABLE IF NOT EXISTS preferences (
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL,
    updated   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    channel   TEXT NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    created   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    text        TEXT NOT NULL,
    next_run    REAL NOT NULL,
    interval    REAL,            -- seconds between runs; NULL = one-off
    active      INTEGER NOT NULL DEFAULT 1,
    created     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (  -- internal state, never shown to the model
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, id);
CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(active, next_run);
"""


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit]


def _like_escape(s: str) -> str:
    """Escape LIKE wildcards so user text matches literally (use ESCAPE '\\')."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def next_daily_run(daily_time: str, after: float) -> float:
    """Next local wall-clock occurrence of 'HH:MM' strictly after `after`.

    Computed from the calendar rather than adding 86400s, so a daily reminder
    stays at the same local time across DST changes.
    """
    hh, mm = (int(x) for x in daily_time.split(":"))
    base = datetime.fromtimestamp(after)
    nxt = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    while nxt.timestamp() <= after:
        nxt = (nxt + timedelta(days=1)).replace(hour=hh, minute=mm)
    return nxt.timestamp()


class Memory:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL improves concurrent read/write; harmless if it can't be set
            # (e.g. :memory: or a filesystem that doesn't support it).
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()
        if self.db_path != ":memory:" and os.name != "nt":
            # Conversations and facts are personal; keep the DB owner-only.
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(reminders)")}
        if "daily_time" not in cols:
            self._conn.execute("ALTER TABLE reminders ADD COLUMN daily_time TEXT")

    # ── facts ──────────────────────────────────────────────────────────
    def remember_fact(self, topic: str, content: str) -> None:
        topic = _clip(topic, 200).strip().lower()
        content = _clip(content, _MAX_FACT).strip()
        if not topic or not content:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO facts(topic, content, created) VALUES (?, ?, ?)",
                (topic, content, time.time()),
            )
            self._conn.commit()

    def recall_facts(self, topic: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 500))
        with self._lock:
            if topic:
                rows = self._conn.execute(
                    "SELECT topic, content, created FROM facts WHERE topic LIKE ? ESCAPE '\\' "
                    "ORDER BY created DESC LIMIT ?",
                    (f"%{_like_escape(_clip(topic, 200).strip().lower())}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT topic, content, created FROM facts ORDER BY created DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def forget_fact(self, content_substring: str) -> int:
        sub = _clip(content_substring, _MAX_FACT).strip()
        if not sub:  # an empty/blank pattern would match (and delete) everything
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM facts WHERE content LIKE ? ESCAPE '\\'", (f"%{_like_escape(sub)}%",)
            )
            self._conn.commit()
            return cur.rowcount

    # ── preferences ────────────────────────────────────────────────────
    def set_preference(self, key: str, value: str) -> None:
        key = _clip(key, 200).strip().lower()
        value = _clip(value, _MAX_PREF).strip()
        if not key:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO preferences(key, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (key, value, time.time()),
            )
            self._conn.commit()

    def get_preferences(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM preferences ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── conversation log ───────────────────────────────────────────────
    def log_message(self, channel: str, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(channel, role, content, created) VALUES (?, ?, ?, ?)",
                (_clip(channel, 200), role, _clip(content, _MAX_MESSAGE), time.time()),
            )
            self._conn.commit()

    def recent_messages(self, channel: str, limit: int = 20) -> list[dict[str, str]]:
        limit = max(1, min(int(limit or 20), 200))
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages WHERE channel = ? ORDER BY id DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_channel(self, channel: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM messages WHERE channel = ?", (channel,))
            self._conn.commit()
            return cur.rowcount

    # ── reminders (used by friday.scheduler) ───────────────────────────
    def add_reminder(
        self,
        channel: str,
        text: str,
        next_run: float,
        interval: float | None = None,
        daily_time: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO reminders(channel, text, next_run, interval, active, created, daily_time) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (_clip(channel, 200), _clip(text, _MAX_FACT), float(next_run),
                 float(interval) if interval else None, time.time(), daily_time),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def due_reminders(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, channel, text, next_run, interval, daily_time FROM reminders "
                "WHERE active = 1 AND next_run <= ? ORDER BY next_run",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_reminders(self, channel: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if channel:
                rows = self._conn.execute(
                    "SELECT id, channel, text, next_run, interval, active, daily_time FROM reminders "
                    "WHERE active = 1 AND channel = ? ORDER BY next_run",
                    (channel,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, channel, text, next_run, interval, active, daily_time FROM reminders "
                    "WHERE active = 1 ORDER BY next_run",
                ).fetchall()
        return [dict(r) for r in rows]

    def reschedule_or_close(
        self,
        reminder_id: int,
        interval: float | None,
        fired_at: float,
        daily_time: str | None = None,
    ) -> None:
        """After firing: bump a recurring reminder, deactivate a one-off."""
        with self._lock:
            if daily_time:
                try:
                    nxt = next_daily_run(daily_time, fired_at)
                except (ValueError, OverflowError, OSError):
                    nxt = fired_at + float(interval or 86_400)
                self._conn.execute("UPDATE reminders SET next_run = ? WHERE id = ?", (nxt, reminder_id))
            elif interval:
                self._conn.execute(
                    "UPDATE reminders SET next_run = ? WHERE id = ?",
                    (fired_at + float(interval), reminder_id),
                )
            else:
                self._conn.execute("UPDATE reminders SET active = 0 WHERE id = ?", (reminder_id,))
            self._conn.commit()

    def cancel_reminder(self, reminder_id: int, channel: str | None = None) -> bool:
        with self._lock:
            if channel is not None:
                cur = self._conn.execute(
                    "UPDATE reminders SET active = 0 WHERE id = ? AND channel = ? AND active = 1",
                    (reminder_id, channel),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE reminders SET active = 0 WHERE id = ? AND active = 1", (reminder_id,)
                )
            self._conn.commit()
            return cur.rowcount > 0

    # ── internal settings (not exposed to the model) ───────────────────
    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ── summary for the system prompt ──────────────────────────────────
    def context_blob(self, max_chars: int = _MAX_CONTEXT_BLOB) -> str:
        """A compact string of what Friday knows, injected into the system prompt.

        Bounded to `max_chars`: preferences first, then the newest facts; each
        entry is clipped so one huge fact can't crowd out everything else.
        """
        prefs = self.get_preferences()
        facts = self.recall_facts(limit=40)
        if not prefs and not facts:
            return "(no stored memory yet)"
        entry_cap = 1_000
        lines: list[str] = []
        used = 0
        omitted = 0

        def add(line: str) -> None:
            nonlocal used, omitted
            if len(line) > entry_cap:
                line = line[:entry_cap] + "…"
            if used + len(line) + 1 > max_chars:
                omitted += 1
                return
            lines.append(line)
            used += len(line) + 1

        if prefs:
            add("Known preferences:")
            for k, v in prefs.items():
                add(f"  - {k}: {v}")
        if facts:
            if prefs:
                add("")
            add("Remembered facts:")
            for f in facts:
                add(f"  - [{f['topic']}] {f['content']}")
        if omitted:
            lines.append(f"  (… {omitted} more entries omitted; use the recall tool to look them up)")
        return "\n".join(lines)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
