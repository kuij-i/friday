"""Configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def _find_env_file() -> Path | None:
    """`.env` in the current directory (or a parent), else next to the source checkout."""
    try:
        from dotenv import find_dotenv
    except ImportError:  # dotenv is optional at runtime
        return None
    found = find_dotenv(usecwd=True)
    if found:
        return Path(found).resolve()
    checkout_env = _PACKAGE_DIR.parent / ".env"
    return checkout_env.resolve() if checkout_env.is_file() else None


_ENV_FILE = _find_env_file()
if _ENV_FILE is not None:
    from dotenv import load_dotenv

    load_dotenv(_ENV_FILE)


def _project_dir() -> Path:
    """Base for relative paths in settings, so they don't depend on the launch directory.

    The folder holding `.env` wins; otherwise the source checkout (if Friday is
    run from one); otherwise the current directory.
    """
    if _ENV_FILE is not None:
        return _ENV_FILE.parent
    checkout = _PACKAGE_DIR.parent
    if (checkout / "run.py").is_file():
        return checkout
    return Path.cwd()


PROJECT_DIR = _project_dir()

PROVIDERS = ("anthropic", "nvidia")
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "nvidia": "nvidia/nemotron-3-super-120b-a12b"}
NVIDIA_DEFAULT_MODEL = DEFAULT_MODELS["nvidia"]
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


_PLACEHOLDER_KEYS = {"sk-ant-...", "nvapi-...", "your-api-key"}


def _real_key(env: str) -> bool:
    value = os.getenv(env, "").strip()
    return bool(value) and value not in _PLACEHOLDER_KEYS


def _provider() -> str:
    """FRIDAY_PROVIDER, or auto: Claude if its key is set, else NVIDIA if that key is."""
    raw = os.getenv("FRIDAY_PROVIDER", "").strip().lower()
    if raw and raw != "auto":
        return raw
    if not _real_key("ANTHROPIC_API_KEY") and _real_key("NVIDIA_API_KEY"):
        return "nvidia"
    return "anthropic"


def _path(env: str, default: str) -> Path:
    raw = (os.getenv(env) or "").strip() or default
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = PROJECT_DIR / p
    return p.resolve()


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


def _env_int(env: str, default: int) -> int:
    value = _as_int(os.getenv(env), default)
    return default if value is None else value


# (env var, attribute, default, min, max) for bounded integer settings.
APPROVAL_TIMEOUT_BOUNDS = (10, 900)
TRUST_MINUTES_BOUNDS = (1, 120)
_BOUNDED_INTS = [
    ("FRIDAY_TELEGRAM_APPROVAL_TIMEOUT", "telegram_approval_timeout", 120, *APPROVAL_TIMEOUT_BOUNDS),
    ("FRIDAY_TRUST_MINUTES", "trust_minutes", 15, *TRUST_MINUTES_BOUNDS),
]


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    return max(bounds[0], min(bounds[1], value))


@dataclass
class Config:
    provider: str = field(default_factory=_provider)
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    nvidia_api_key: str = field(default_factory=lambda: os.getenv("NVIDIA_API_KEY", "").strip())
    nvidia_base_url: str = field(
        default_factory=lambda: os.getenv("NVIDIA_BASE_URL", "").strip() or NVIDIA_BASE_URL
    )
    # Empty means "the provider's default" (see `model_name`).
    model: str = field(default_factory=lambda: os.getenv("FRIDAY_MODEL", "").strip())
    effort: str = field(default_factory=lambda: os.getenv("FRIDAY_EFFORT", "").strip().lower() or "high")

    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_owner_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_OWNER_ID", "").strip())

    auto_approve: bool = field(default_factory=lambda: _as_bool(os.getenv("FRIDAY_AUTO_APPROVE"), False))
    # Telegram: seconds an approval request waits before it is denied, and the
    # default length of /trust_on. Use the effective_* properties (clamped).
    telegram_approval_timeout: int = field(default_factory=lambda: _env_int("FRIDAY_TELEGRAM_APPROVAL_TIMEOUT", 120))
    trust_minutes: int = field(default_factory=lambda: _env_int("FRIDAY_TRUST_MINUTES", 15))
    enable_web_search: bool = field(default_factory=lambda: _as_bool(os.getenv("FRIDAY_WEB_SEARCH"), True))
    web_search_max_uses: int = field(default_factory=lambda: _as_int(os.getenv("FRIDAY_WEB_SEARCH_MAX_USES"), 5) or 5)
    whisper_model: str = field(default_factory=lambda: os.getenv("FRIDAY_WHISPER_MODEL", "base"))
    enable_self_coding: bool = field(default_factory=lambda: _as_bool(os.getenv("FRIDAY_SELF_CODING"), False))
    repo_root: Path = field(default_factory=lambda: _path("FRIDAY_REPO_ROOT", "."))
    workspace: Path = field(default_factory=lambda: _path("FRIDAY_WORKSPACE", "./workspace"))
    db_path: Path = field(default_factory=lambda: _path("FRIDAY_DB", "./data/friday.db"))

    # Max tool-use iterations per user turn (keeps runaway loops bounded).
    max_iterations: int = 40

    def __post_init__(self) -> None:
        if not self.model:
            self.model = DEFAULT_MODELS.get(self.provider, DEFAULT_MODELS["anthropic"])

    @property
    def web_search_available(self) -> bool:
        """Web search is Claude's server-side tool; other providers don't have it."""
        return self.enable_web_search and self.provider == "anthropic"

    @property
    def effective_approval_timeout(self) -> int:
        return _clamp(self.telegram_approval_timeout, APPROVAL_TIMEOUT_BOUNDS)

    @property
    def effective_trust_minutes(self) -> int:
        return _clamp(self.trust_minutes, TRUST_MINUTES_BOUNDS)

    def ensure_dirs(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.provider not in PROVIDERS:
            problems.append(f"FRIDAY_PROVIDER '{self.provider}' is invalid; use anthropic or nvidia.")
        elif self.provider == "nvidia":
            if not self.nvidia_api_key:
                problems.append("NVIDIA_API_KEY is not set (get one at build.nvidia.com and put it in .env).")
            elif self.nvidia_api_key in _PLACEHOLDER_KEYS:
                problems.append("NVIDIA_API_KEY is still the placeholder from .env.example — paste your real key.")
        elif not self.anthropic_api_key:
            problems.append(
                "No API key set: put ANTHROPIC_API_KEY (Claude) or NVIDIA_API_KEY (NVIDIA) in .env "
                "(copy .env.example to .env first)."
            )
        elif self.anthropic_api_key in _PLACEHOLDER_KEYS:
            problems.append("ANTHROPIC_API_KEY is still the placeholder from .env.example — paste your real key.")
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            problems.append(f"FRIDAY_EFFORT '{self.effort}' is invalid; use low|medium|high|xhigh|max.")
        owner = self.telegram_owner_id
        if owner and not (owner.isdigit() and int(owner) > 0):
            problems.append(
                f"TELEGRAM_OWNER_ID '{owner}' must be your numeric Telegram user id (a positive number)."
            )
        raw_uses = os.getenv("FRIDAY_WEB_SEARCH_MAX_USES")
        if raw_uses is not None and raw_uses.strip() and _as_int(raw_uses, None) is None:
            problems.append(f"FRIDAY_WEB_SEARCH_MAX_USES '{raw_uses}' is not an integer (using 5).")
        if self.web_search_max_uses < 1:
            problems.append("FRIDAY_WEB_SEARCH_MAX_USES must be at least 1.")
        for env, attr, default, lo, hi in _BOUNDED_INTS:
            raw = os.getenv(env)
            if raw is not None and raw.strip() and _as_int(raw, None) is None:
                problems.append(f"{env} '{raw}' is not an integer (using {default}).")
            elif not lo <= getattr(self, attr) <= hi:
                problems.append(f"{env} must be between {lo} and {hi} (got {getattr(self, attr)}).")
        if self.workspace == Path(self.workspace.anchor):
            problems.append("FRIDAY_WORKSPACE must not be a filesystem root — scope it to a project dir.")
        return problems


config = Config()
