#!/usr/bin/env python
"""Friday entrypoint.

Usage:
    python run.py cli        # chat with Friday in this terminal
    python run.py telegram   # run the Telegram bot (talk from your phone)
    python run.py doctor     # check configuration
"""
from __future__ import annotations

import sys

from friday.config import config


def doctor() -> None:
    config.ensure_dirs()
    print(f"model:      {config.model}")
    print(f"effort:     {config.effort}")
    print(f"workspace:  {config.workspace}")
    print(f"database:   {config.db_path}")
    print(f"auto_approve: {config.auto_approve}")
    print(f"telegram:   {'configured' if config.telegram_token else 'not set'}")
    problems = config.validate()
    if problems:
        print("\nproblems:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nconfig looks good.")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "cli"
    if mode == "cli":
        from friday.channels.cli import run_cli

        run_cli(config)
    elif mode == "telegram":
        from friday.channels.telegram_bot import run_telegram

        run_telegram(config)
    elif mode == "doctor":
        doctor()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
