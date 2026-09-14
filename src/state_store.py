"""Durable state: what was sent, what is awaiting an answer, what was missed.

This is the file that makes a stateless cloud runner safe. Without it, every
run would re-send the same reminders. It is committed back to the repository by
the workflow, which also gives you a plain-text history of your own days.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("human_os.state")

RETENTION_DAYS = 60


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "sent": {},
            "pending_acks": [],
            "day_log": {},
            "telegram_offset": None,
            "last_run": None,
        }
        self._original = ""
        self.load()

    # ---------- persistence ----------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._original = self.path.read_text(encoding="utf-8")
            loaded = json.loads(self._original)
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt state file must never stop the morning briefing.
            log.warning("State file unreadable (%s); starting fresh.", exc)
            return
        if isinstance(loaded, dict):
            self.data.update(loaded)

    def save(self) -> bool:
        """Atomic write. Returns True when the file actually changed."""
        payload = json.dumps(self.data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if payload == self._original:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False, suffix=".tmp"
        )
        try:
            handle.write(payload)
            handle.close()
            os.replace(handle.name, self.path)
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise
        self._original = payload
        return True

    # ---------- de-duplication ----------

    def was_sent(self, key: str) -> bool:
        return key in self.data["sent"]

    def mark_sent(self, key: str, now: datetime, event_id: str, template: str) -> None:
        self.data["sent"][key] = {
            "at": now.isoformat(timespec="seconds"),
            "event_id": event_id,
            "template": template,
        }

    def sent_today(self, day_iso: str) -> list[str]:
        return sorted(k for k in self.data["sent"] if k.startswith(f"{day_iso}:"))

    # ---------- acknowledgements ----------

    @property
    def pending_acks(self) -> list[dict[str, Any]]:
        return self.data["pending_acks"]

    def add_pending_ack(self, key: str, event_id: str, now: datetime, day_iso: str) -> None:
        if any(item["key"] == key for item in self.pending_acks):
            return
        self.pending_acks.append(
            {
                "key": key,
                "event_id": event_id,
                "day": day_iso,
                "asked_at": now.isoformat(timespec="seconds"),
                "escalations_sent": 0,
            }
        )

    def resolve_acks(self, status: str, now: datetime) -> list[dict[str, Any]]:
        """Close every open check-in. Returns the ones that were open."""
        resolved = list(self.pending_acks)
        for item in resolved:
            self.log_day(item["day"], item["event_id"], status, now)
        self.data["pending_acks"] = []
        return resolved

    def expire_acks(self, now: datetime, expire_after_minutes: int) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        expired: list[dict[str, Any]] = []
        cutoff = timedelta(minutes=expire_after_minutes)
        for item in self.pending_acks:
            asked_at = datetime.fromisoformat(item["asked_at"])
            if now - asked_at > cutoff:
                self.log_day(item["day"], item["event_id"], "no_response", now)
                expired.append(item)
            else:
                kept.append(item)
        self.data["pending_acks"] = kept
        return expired

    # ---------- daily log (drives the recovery protocol) ----------

    def log_day(self, day_iso: str, event_id: str, status: str, now: datetime) -> None:
        day = self.data["day_log"].setdefault(day_iso, {})
        day[event_id] = {"status": status, "at": now.isoformat(timespec="seconds")}

    def day_status(self, day_iso: str, event_suffix: str = "mvd") -> str | None:
        """Status of the day's MVD check-in: done, skip, no_response, or None."""
        for event_id, record in (self.data["day_log"].get(day_iso) or {}).items():
            if event_id.endswith(event_suffix):
                return record.get("status")
        return None

    def streak(self, today_iso: str) -> int:
        """Consecutive days ending yesterday where the MVD was marked done."""
        count = 0
        day = datetime.fromisoformat(today_iso).date()
        while True:
            day -= timedelta(days=1)
            if self.day_status(day.isoformat()) != "done":
                return count
            count += 1
            if count > 365:
                return count

    # ---------- misc ----------

    @property
    def telegram_offset(self) -> int | None:
        return self.data.get("telegram_offset")

    @telegram_offset.setter
    def telegram_offset(self, value: int) -> None:
        self.data["telegram_offset"] = value

    def touch(self, now: datetime) -> None:
        self.data["last_run"] = now.isoformat(timespec="seconds")

    def prune(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=RETENTION_DAYS)).date().isoformat()
        self.data["sent"] = {
            key: value for key, value in self.data["sent"].items() if key.split(":", 1)[0] >= cutoff
        }
        self.data["day_log"] = {
            day: value for day, value in self.data["day_log"].items() if day >= cutoff
        }
