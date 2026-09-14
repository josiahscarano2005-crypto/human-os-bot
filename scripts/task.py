"""Manage the task list from the laptop.

    python scripts\\task.py                        show open tasks
    python scripts\\task.py add "mail the form @fri"
    python scripts\\task.py add "!submit ANSC quiz @today"
    python scripts\\task.py done 3
    python scripts\\task.py drop 3

Texting the bot (`+ mail the form`) is usually faster. This exists for when you
are already at the keyboard. If you add tasks locally, commit and push
`state/tasks.json` so the cloud runner sees them.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import time_utils as tu  # noqa: E402
from src.config_loader import load_config  # noqa: E402
from src.task_store import TaskStore  # noqa: E402


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = load_config(ROOT / "config")
    now = tu.now_local(cfg.tz)
    tasks = TaskStore(ROOT / "state" / "tasks.json")

    command = (argv[0].lower() if argv else "list")
    argument = " ".join(argv[1:]).strip()

    if command in ("list", "ls"):
        print(tasks.render_list(now.date()))
        return 0

    if command == "add":
        if not argument:
            print('Nothing to add. Try: python scripts\\task.py add "mail the form @fri"')
            return 1
        task = tasks.add(argument, now, source="cli")
        print(f"Added {task.id}. {task.label(now.date())}")
    elif command in ("done", "drop"):
        try:
            task_id = int(argument.lstrip("#"))
        except ValueError:
            print(f"Need a task number, e.g. python scripts\\task.py {command} 3")
            return 1
        task = tasks.complete(task_id, now) if command == "done" else tasks.drop(task_id)
        if not task:
            print(f"No open task {task_id}.")
            return 1
        print(f"{'Closed' if command == 'done' else 'Dropped'}: {task.text}")
    else:
        print(__doc__)
        return 1

    tasks.save()
    print()
    print(tasks.render_list(now.date()))
    print("\nRemember to commit state/tasks.json so the cloud runner sees this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
