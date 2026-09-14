"""Compatibility entry point.

The old version of this file was a long-running `schedule` loop, which cannot
work on a cron runner that only lives for a few minutes. The real program is
now src/main.py. This shim exists so that any old command, workflow or muscle
memory that runs `python bot.py` still does the right thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
