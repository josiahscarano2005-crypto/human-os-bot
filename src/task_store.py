"""A running task list you can add to from your phone.

Capture has to cost nothing. If adding a task takes more than one line of text,
it does not get added, and the task list stops reflecting reality. So the entry
points are a Telegram message (`/add pick up prescription`) or a `+` prefix,
and everything else - priority, due date - is optional suffix sugar that can be
ignored entirely.

Tasks live in state/tasks.json, which the workflow commits back to the repo, so
the list survives every run and is readable as plain text.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# `@today`, `@tomorrow`, `@fri`, `@2026-09-20` anywhere in the text.
DUE_PATTERN = re.compile(r"@(today|tomorrow|tmw|mon|tue|wed|thu|fri|sat|sun|\d{4}-\d{2}-\d{2})\b", re.I)
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Nudge thresholds, in days open.
STALE_DAYS = 4
ANCIENT_DAYS = 8


@dataclass
class Task:
    id: int
    text: str
    created: str
    due: str | None = None
    starred: bool = False
    done: bool = False
    done_at: str | None = None
    source: str = "telegram"

    @property
    def is_open(self) -> bool:
        return not self.done

    def age_days(self, today: date) -> int:
        return (today - date.fromisoformat(self.created[:10])).days

    def due_date(self) -> date | None:
        return date.fromisoformat(self.due) if self.due else None

    def label(self, today: date) -> str:
        """One line, scannable: star, text, due marker, age marker."""
        parts = []
        if self.starred:
            parts.append("TOP:")
        parts.append(self.text)
        due = self.due_date()
        if due:
            delta = (due - today).days
            if delta < 0:
                parts.append("(late)")
            elif delta == 0:
                parts.append("(today)")
            elif delta == 1:
                parts.append("(tomorrow)")
            else:
                parts.append(f"({due.strftime('%a')})")
        age = self.age_days(today)
        if age >= ANCIENT_DAYS:
            parts.append(f"[{age}d - shrink it or drop it]")
        elif age >= STALE_DAYS:
            parts.append(f"[{age}d]")
        return " ".join(parts)


def parse_entry(raw: str, today: date) -> tuple[str, str | None, bool]:
    """Pull an optional due date and priority flag out of free text."""
    text = raw.strip()
    starred = False
    if text.startswith("!"):
        starred = True
        text = text[1:].strip()

    due: str | None = None
    match = DUE_PATTERN.search(text)
    if match:
        token = match.group(1).lower()
        text = DUE_PATTERN.sub("", text).strip()
        if token == "today":
            due = today.isoformat()
        elif token in ("tomorrow", "tmw"):
            due = (today + timedelta(days=1)).isoformat()
        elif token in WEEKDAY_NAMES:
            target = WEEKDAY_NAMES.index(token)
            ahead = (target - today.weekday()) % 7 or 7
            due = (today + timedelta(days=ahead)).isoformat()
        else:
            due = token

    return re.sub(r"\s+", " ", text).strip(), due, starred


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.tasks: list[Task] = []
        self.next_id = 1
        self._original = ""
        self.load()

    # ---------- persistence ----------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._original = self.path.read_text(encoding="utf-8")
            payload = json.loads(self._original)
        except (OSError, json.JSONDecodeError):
            return
        self.tasks = [Task(**item) for item in payload.get("tasks", [])]
        self.next_id = int(payload.get("next_id", len(self.tasks) + 1))

    def save(self) -> bool:
        payload = json.dumps(
            {"next_id": self.next_id, "tasks": [asdict(t) for t in self.tasks]},
            indent=2,
            ensure_ascii=False,
        ) + "\n"
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

    # ---------- operations ----------

    def add(self, raw: str, now: datetime, source: str = "telegram") -> Task:
        text, due, starred = parse_entry(raw, now.date())
        task = Task(
            id=self.next_id,
            text=text,
            created=now.isoformat(timespec="seconds"),
            due=due,
            starred=starred,
            source=source,
        )
        self.next_id += 1
        self.tasks.append(task)
        return task

    def get(self, task_id: int) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def complete(self, task_id: int, now: datetime) -> Task | None:
        task = self.get(task_id)
        if task and task.is_open:
            task.done = True
            task.done_at = now.isoformat(timespec="seconds")
            return task
        return None

    def drop(self, task_id: int) -> Task | None:
        task = self.get(task_id)
        if task:
            self.tasks.remove(task)
        return task

    def open_tasks(self, today: date) -> list[Task]:
        """Starred first, then anything due soonest, then oldest."""

        def sort_key(task: Task) -> tuple[int, date, str]:
            due = task.due_date() or date.max
            return (0 if task.starred else 1, due, task.created)

        return sorted((t for t in self.tasks if t.is_open), key=sort_key)

    def completed_on(self, day: date) -> list[Task]:
        stamp = day.isoformat()
        return [t for t in self.tasks if t.done and (t.done_at or "").startswith(stamp)]

    def prune(self, now: datetime, keep_days: int = 30) -> None:
        cutoff = (now.date() - timedelta(days=keep_days)).isoformat()
        self.tasks = [t for t in self.tasks if t.is_open or (t.done_at or "")[:10] >= cutoff]

    # ---------- rendering ----------

    def render_list(self, today: date, limit: int | None = None) -> str:
        open_tasks = self.open_tasks(today)
        if not open_tasks:
            return "No open tasks. If something is on your mind, send it now."
        shown = open_tasks if limit is None else open_tasks[:limit]
        lines = [f"{task.id}. {task.label(today)}" for task in shown]
        hidden = len(open_tasks) - len(shown)
        if hidden > 0:
            lines.append(f"...and {hidden} more - /tasks for all")
        return "\n".join(lines)

    def top_for_briefing(self, today: date, count: int = 3) -> list[str]:
        """Tasks that have earned a slot in the morning's top 3."""
        picks: list[str] = []
        for task in self.open_tasks(today):
            due = task.due_date()
            urgent = task.starred or (due is not None and due <= today)
            stale = task.age_days(today) >= STALE_DAYS
            if urgent or stale:
                picks.append(task.label(today))
            if len(picks) >= count:
                break
        return picks
