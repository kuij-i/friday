"""Persistent memory for Friday.

Three stores, all in one SQLite file:
  * facts        — durable things Friday learns ("my main project is X").
  * preferences  — key/value settings about how you like to work.
  * messages     — rolling conversation log per channel, for continuity.

This is the heart of phase-1 self-improvement: Friday gets more useful over
time because it remembers you, not because it rewrites its own code.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

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
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, id);
CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic);
"""


class Memory:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── facts ──────────────────────────────────────────────────────────
    def remember_fact(self, topic: str, content: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO facts(topic, content, created) VALUES (?, ?, ?)",
            (topic.strip().lower(), content.strip(), time.time()),
        )
        self._conn.commit()

    def recall_facts(self, topic: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if topic:
            rows = self._conn.execute(
                "SELECT topic, content, created FROM facts WHERE topic LIKE ? "
                "ORDER BY created DESC LIMIT ?",
                (f"%{topic.strip().lower()}%", limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT topic, content, created FROM facts ORDER BY created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def forget_fact(self, content_substring: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM facts WHERE content LIKE ?", (f"%{content_substring}%",)
        )
        self._conn.commit()
        return cur.rowcount

    # ── preferences ────────────────────────────────────────────────────
    def set_preference(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO preferences(key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key.strip().lower(), value.strip(), time.time()),
        )
        self._conn.commit()

    def get_preferences(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM preferences").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── conversation log ───────────────────────────────────────────────
    def log_message(self, channel: str, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO messages(channel, role, content, created) VALUES (?, ?, ?, ?)",
            (channel, role, content, time.time()),
        )
        self._conn.commit()

    def recent_messages(self, channel: str, limit: int = 20) -> list[dict[str, str]]:
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE channel = ? ORDER BY id DESC LIMIT ?",
            (channel, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_channel(self, channel: str) -> int:
        cur = self._conn.execute("DELETE FROM messages WHERE channel = ?", (channel,))
        self._conn.commit()
        return cur.rowcount

    # ── summary for the system prompt ──────────────────────────────────
    def context_blob(self) -> str:
        """A compact string of what Friday knows, injected into the system prompt."""
        prefs = self.get_preferences()
        facts = self.recall_facts(limit=40)
        parts: list[str] = []
        if prefs:
            parts.append("Known preferences:\n" + "\n".join(f"  - {k}: {v}" for k, v in prefs.items()))
        if facts:
            parts.append(
                "Remembered facts:\n"
                + "\n".join(f"  - [{f['topic']}] {f['content']}" for f in facts)
            )
        return "\n\n".join(parts) if parts else "(no stored memory yet)"

    def close(self) -> None:
        self._conn.close()
