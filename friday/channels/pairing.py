"""Telegram owner resolution and one-time pairing codes.

Kept free of `telegram` imports so `doctor` and tests can use it cheaply.

Owner sources, in order of precedence:
  1. TELEGRAM_OWNER_ID in .env (validated by Config.validate()).
  2. An owner paired with a one-time code shown on the local terminal
     (settings key PAIRED_SETTING).
An id stored under LEGACY_SETTING was claimed silently by an older Friday
version ("first chatter wins"); it is reported but never trusted.
"""
from __future__ import annotations

import enum
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Config
from ..memory import Memory

log = logging.getLogger("friday.telegram")

PAIRED_SETTING = "telegram_paired_owner_id"
LEGACY_SETTING = "telegram_owner_id"

# No 0/O, 1/I/L: easy to read off a screen and type on a phone.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL_SECONDS = 600
MAX_ATTEMPTS = 5


class PairResult(enum.Enum):
    OK = "ok"
    WRONG = "wrong"
    EXPIRED = "expired"
    LOCKED = "locked"  # too many wrong attempts
    USED = "used"


def _normalise(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


class PairingCode:
    """A single-use code, valid for CODE_TTL_SECONDS, with MAX_ATTEMPTS wrong tries in total."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
        self._expires = self._clock() + CODE_TTL_SECONDS
        self._wrong = 0
        self._used = False

    @property
    def display(self) -> str:
        return f"{self._code[:4]}-{self._code[4:]}"

    @property
    def open(self) -> bool:
        return not self._used and self._wrong < MAX_ATTEMPTS and self._clock() < self._expires

    def check(self, attempt: str) -> PairResult:
        if self._used:
            return PairResult.USED
        if self._wrong >= MAX_ATTEMPTS:
            return PairResult.LOCKED
        if self._clock() >= self._expires:
            return PairResult.EXPIRED
        if secrets.compare_digest(_normalise(attempt or "").encode(), self._code.encode()):
            self._used = True
            return PairResult.OK
        self._wrong += 1
        if self._wrong >= MAX_ATTEMPTS:
            log.warning(
                "Pairing code invalidated after %d wrong attempts. Restart Friday to get a new one.",
                MAX_ATTEMPTS,
            )
        return PairResult.WRONG


@dataclass
class OwnerStatus:
    owner_id: int | None
    source: str | None  # "env" | "paired" | None
    legacy_claim: str | None = None


def _as_user_id(value: str | None) -> int | None:
    if value and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def resolve_owner(config: Config, memory: Memory | None) -> OwnerStatus:
    """Who owns the bot. `memory` may be None (no database yet)."""
    legacy = memory.get_setting(LEGACY_SETTING) if memory else None
    env_owner = _as_user_id(config.telegram_owner_id)
    if env_owner is not None:
        return OwnerStatus(env_owner, "env", legacy)
    paired = _as_user_id(memory.get_setting(PAIRED_SETTING)) if memory else None
    if paired is not None:
        return OwnerStatus(paired, "paired", legacy)
    return OwnerStatus(None, None, legacy)


def owner_status_line(status: OwnerStatus) -> str:
    if status.source == "env":
        return f"owner: {status.owner_id} (from .env)"
    if status.source == "paired":
        return f"owner: {status.owner_id} (paired, pin it in .env)"
    return "owner: NOT SET — run `python run.py telegram` and pair"


def legacy_claim_message(claim: str) -> str:
    return (
        f"Found an owner id {claim} claimed by an older Friday version. If that is you, set "
        f"TELEGRAM_OWNER_ID={claim} in .env; otherwise delete it with "
        "`python run.py telegram --reset-owner`"
    )


def reset_owner(memory: Memory) -> list[str]:
    """Forget any stored owner (paired or legacy). Returns the keys removed."""
    return [key for key in (PAIRED_SETTING, LEGACY_SETTING) if memory.delete_setting(key)]
