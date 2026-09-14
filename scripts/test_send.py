"""Send one test message using .env or environment variables.

    python scripts/test_send.py
    python scripts/test_send.py "custom text"

Use this to prove delivery works without waiting for 6:30 AM. It touches no
state, so it can never consume a real reminder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import time_utils as tu  # noqa: E402
from src.config_loader import ConfigError, load_config  # noqa: E402
from src.telegram_sender import TelegramError, TelegramSender, mask  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    print(f"Token: {mask(token)}   Chat id: {chat_id or '<missing>'}")

    try:
        cfg = load_config(ROOT / "config")
        now = tu.now_local(cfg.tz)
        text = " ".join(sys.argv[1:]) or cfg.render(
            "test_message", now=tu.fmt_datetime(now), mode="manual test"
        )
    except ConfigError as exc:
        print("Config problem, sending a plain fallback message instead:")
        print(exc)
        text = "Human OS test message."

    try:
        sender = TelegramSender(token=token, chat_id=chat_id)
        message_id = sender.send(text)
    except TelegramError as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print(f"\nSent. Telegram message id {message_id}. Check your phone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
