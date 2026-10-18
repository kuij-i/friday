"""Configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional at runtime
    pass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int | None) -> int | None:
    """Lenient int parse: a typo in .env must not crash import (and `doctor`)."""
    try:
        return int(str(value).strip()) if value is not None and str(value).strip() else default
    except ValueError:
        return default


@dataclass
class Config:
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("FRIDAY_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.getenv("FRIDAY_EFFORT", "high"))

    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_owner_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_OWNER_ID", "").strip())

    auto_approve: bool = field(default_factory=lambda: _as_bool(os.getenv("FRIDAY_AUTO_APPROVE"), False))
    enable_web_search: bool = field(default_factory=lambda: _as_bool(os.getenv("FRIDAY_WEB_SEARCH"), True))
    web_search_max_uses: int = field(default_factory=lambda: _as_int(os.getenv("FRIDAY_WEB_SEARCH_MAX_USES"), 5) or 5)
    whisper_model: str = field(default_factory=lambda: os.getenv("FRIDAY_WHISPER_MODEL", "base"))
    enable_self_coding: bool = field(default_factory=lambda: _as_bool(os.getenv("FRIDAY_SELF_CODING"), False))
    repo_root: Path = field(default_factory=lambda: Path(os.getenv("FRIDAY_REPO_ROOT", ".")).resolve())
    workspace: Path = field(default_factory=lambda: Path(os.getenv("FRIDAY_WORKSPACE", "./workspace")).resolve())
    db_path: Path = field(default_factory=lambda: Path(os.getenv("FRIDAY_DB", "./data/friday.db")).resolve())

    # Max tool-use iterations per user turn (keeps runaway loops bounded).
    max_iterations: int = 40

    def ensure_dirs(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is not set (copy .env.example to .env and fill it in).")
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            problems.append(f"FRIDAY_EFFORT '{self.effort}' is invalid; use low|medium|high|xhigh|max.")
        if self.telegram_owner_id and not self.telegram_owner_id.lstrip("-").isdigit():
            problems.append(f"TELEGRAM_OWNER_ID '{self.telegram_owner_id}' must be a numeric Telegram user id.")
        raw_uses = os.getenv("FRIDAY_WEB_SEARCH_MAX_USES")
        if raw_uses is not None and raw_uses.strip() and _as_int(raw_uses, None) is None:
            problems.append(f"FRIDAY_WEB_SEARCH_MAX_USES '{raw_uses}' is not an integer (using 5).")
        if self.web_search_max_uses < 1:
            problems.append("FRIDAY_WEB_SEARCH_MAX_USES must be at least 1.")
        if self.workspace == Path(self.workspace.anchor):
            problems.append("FRIDAY_WORKSPACE must not be a filesystem root — scope it to a project dir.")
        return problems


config = Config()
