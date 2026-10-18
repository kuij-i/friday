#!/usr/bin/env python
"""Friday entrypoint — see `python run.py help`. Thin shim over friday.__main__."""
from __future__ import annotations

from friday.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
