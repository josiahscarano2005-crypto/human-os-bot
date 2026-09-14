"""Telegram transport: retries, rate limits, and no secrets in the logs."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger("human_os.telegram")

API_ROOT = "https://api.telegram.org"
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 4
TELEGRAM_MAX_CHARS = 4096


class TelegramError(Exception):
    pass


def mask(token: str) -> str:
    """Only ever show enough of a token to tell two of them apart."""
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def sanitize_secret(value: str, name: str = "") -> str:
    """Recover from the usual copy-paste damage.

    A secret pasted into GitHub's web form often arrives as
    `TELEGRAM_BOT_TOKEN=123:abc`, wrapped in quotes, or with a trailing
    newline. Telegram answers all of those with a bare 404, so clean them up
    here rather than losing a morning to it.
    """
    cleaned = (value or "").strip().strip("\"'").strip()
    prefix = f"{name}="
    if name and cleaned.upper().startswith(prefix.upper()):
        cleaned = cleaned[len(prefix) :].strip().strip("\"'").strip()
    return cleaned


def describe_token(token: str) -> str:
    """Describe a token's shape for troubleshooting, without revealing it."""
    if not token:
        return "empty"
    if ":" not in token:
        return f"{len(token)} chars, no ':' separator - this does not look like a bot token"
    bot_id, _, secret = token.partition(":")
    shape = f"{len(token)} chars, bot id {len(bot_id)} digits, secret {len(secret)} chars"
    if not bot_id.isdigit():
        shape += " - the part before ':' should be all digits"
    return shape


def harden_markdown(text: str) -> str:
    """Neutralise stray formatting characters in message text.

    Telegram's legacy Markdown treats a lone `_` as an unterminated italic tag
    and rejects the whole message, so a room number like `HS_126` or a timezone
    like `America/New_York` would cost a reminder. Bold via `*` is the only
    formatting these templates use, so underscores are always escaped, and
    asterisks are escaped only when they cannot possibly pair up.
    """
    text = text.replace("_", r"\_")
    if text.count("*") % 2 == 1:
        text = text.replace("*", r"\*")
    return text


class TelegramSender:
    def __init__(
        self,
        token: str,
        chat_id: str,
        dry_run: bool = False,
        session: requests.Session | None = None,
    ) -> None:
        # Dry runs are for checking wording and timing, so they must work on a
        # machine that has no credentials at all.
        if not dry_run:
            if not token or token.startswith("paste_"):
                raise TelegramError(
                    "TELEGRAM_BOT_TOKEN is missing. Set it in .env locally or as a GitHub Secret."
                )
            if not chat_id or chat_id.startswith("paste_"):
                raise TelegramError(
                    "TELEGRAM_CHAT_ID is missing. Set it in .env locally or as a GitHub Secret."
                )
        self._token = sanitize_secret(token, "TELEGRAM_BOT_TOKEN")
        self.chat_id = sanitize_secret(str(chat_id), "TELEGRAM_CHAT_ID")
        self.dry_run = dry_run
        self._session = session or requests.Session()

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self._token}/{method}"

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._session.post(
                    self._url(method), json=payload, timeout=TIMEOUT_SECONDS
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                log.warning("%s attempt %s failed (%s)", method, attempt, last_error)
                time.sleep(min(2**attempt, 15))
                continue

            if response.status_code == 200:
                return response.json()

            body = response.text[:400]

            if response.status_code == 429:
                retry_after = 5
                try:
                    retry_after = int(response.json()["parameters"]["retry_after"])
                except Exception:
                    pass
                log.warning("Rate limited by Telegram, waiting %ss", retry_after)
                time.sleep(min(retry_after + 1, 60))
                last_error = f"429 rate limited: {body}"
                continue

            if response.status_code in (401, 404):
                raise TelegramError(
                    f"Telegram rejected the token ({response.status_code}). "
                    f"Token shape: {describe_token(self._token)}. "
                    "The value is wrong, truncated, or was revoked. Re-copy it from "
                    "@BotFather and update .env locally or the TELEGRAM_BOT_TOKEN "
                    "secret on GitHub - paste the token only, with no name, quotes "
                    "or trailing spaces."
                )

            if response.status_code == 400 and "parse entities" in body:
                # A stray * or _ in a message must never cost a reminder.
                raise _ParseModeError(body)

            if 500 <= response.status_code < 600:
                last_error = f"{response.status_code}: {body}"
                log.warning("Telegram server error, retrying: %s", last_error)
                time.sleep(min(2**attempt, 15))
                continue

            raise TelegramError(f"Telegram API error {response.status_code}: {body}")

        raise TelegramError(f"{method} failed after {MAX_ATTEMPTS} attempts: {last_error}")

    def send(self, text: str, parse_mode: str | None = "Markdown") -> int | None:
        """Send one message. Returns the Telegram message id, or None in dry-run."""
        text = text.strip()
        if not text:
            return None
        if parse_mode == "Markdown":
            text = harden_markdown(text)
        if len(text) > TELEGRAM_MAX_CHARS:
            text = text[: TELEGRAM_MAX_CHARS - 20].rstrip() + "\n...[truncated]"

        if self.dry_run:
            log.info("DRY RUN - would send:\n%s\n%s", "-" * 48, text)
            return None

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            result = self._call("sendMessage", payload)
        except _ParseModeError:
            log.warning("Markdown parse failed; resending as plain text.")
            payload.pop("parse_mode", None)
            result = self._call("sendMessage", payload)

        return (result.get("result") or {}).get("message_id")

    def get_updates(self, offset: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch replies so the bot can see 'done' / 'skip' / '/status'."""
        payload: dict[str, Any] = {"limit": limit, "timeout": 0}
        if offset is not None:
            payload["offset"] = offset
        try:
            result = self._call("getUpdates", payload)
        except TelegramError as exc:
            log.warning("Could not fetch updates: %s", exc)
            return []
        return result.get("result") or []


class _ParseModeError(Exception):
    """Internal: Telegram could not parse the Markdown in this message."""
