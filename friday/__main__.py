"""Friday entrypoint.

Usage:
    python run.py cli        # chat with Friday in this terminal (default)
    python run.py telegram   # run the Telegram bot (talk from your phone)
    python run.py doctor     # check configuration
    python run.py version    # print the version

`python -m friday <mode>` and the installed `friday <mode>` command work too.
"""
from __future__ import annotations

import sys


def doctor() -> int:
    from . import __version__
    from .config import config

    print(f"friday:     {__version__} (python {sys.version.split()[0]})")
    print(f"provider:   {config.provider}")
    print(f"model:      {config.model}")
    if config.provider == "anthropic":
        print(f"effort:     {config.effort}")
    print(f"workspace:  {config.workspace}")
    print(f"database:   {config.db_path}")
    print(f"auto_approve: {config.auto_approve}")
    print(f"web_search: {config.web_search_available}")
    print(f"self_coding: {config.enable_self_coding}")
    print(f"telegram:   {'configured' if config.telegram_token else 'not set'}")
    problems = config.validate()
    try:
        config.ensure_dirs()
    except OSError as e:
        problems.append(f"Can't create workspace/data directories: {e}")
    if problems:
        print("\nproblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nconfig looks good.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    mode = args[0].lower() if args else "cli"
    if mode == "cli":
        from .channels.cli import run_cli
        from .config import config

        return run_cli(config)
    if mode == "telegram":
        from .channels.telegram_bot import run_telegram
        from .config import config

        return run_telegram(config)
    if mode == "doctor":
        return doctor()
    if mode in {"version", "--version", "-v"}:
        from . import __version__

        print(__version__)
        return 0
    print(__doc__)
    return 0 if mode in {"help", "--help", "-h"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
