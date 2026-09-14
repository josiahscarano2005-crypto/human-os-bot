"""Check every YAML file before it can break a morning.

    python scripts/validate_config.py

Exits non-zero with a list of problems. The GitHub workflow runs this on every
push, so a bad edit fails in the browser instead of at 6:30 AM.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import time_utils as tu  # noqa: E402
from src.config_loader import ConfigError, load_config  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        cfg = load_config(ROOT / "config")
    except ConfigError as exc:
        print("CONFIG INVALID\n")
        print(exc)
        return 1

    total_events = sum(len(day.events) for day in cfg.days.values())
    active = sum(
        1
        for day in cfg.days.values()
        for event in day.events
        if cfg.variant_active(event.variant)
    )

    print("CONFIG OK")
    print(f"  timezone         {cfg.timezone}")
    print(f"  templates        {len(cfg.templates)}")
    print(f"  events           {total_events} defined / {active} active")
    print(f"  active variants  {', '.join(sorted(cfg.active_variants)) or 'none'}")
    print(f"  grace window     {cfg.grace_minutes} min")
    print(f"  deadlines        {len(cfg.deadlines)} ({sum(1 for d in cfg.deadlines if d.is_open)} open)")

    warnings: list[str] = []

    for day_key, day in cfg.days.items():
        times = Counter(
            event.time for event in day.events if cfg.variant_active(event.variant)
        )
        for time_str, count in times.items():
            if count > 1:
                warnings.append(
                    f"{day_key}: {count} active messages both fire at {time_str}. "
                    "Two notifications in the same minute get ignored as one."
                )
        if not day.events:
            warnings.append(f"{day_key}: no events at all.")

    unused = set(cfg.templates) - {
        event.template for day in cfg.days.values() for event in day.events
    } - {"deadline_alert", "recovery_block", "ack_ok", "ack_skip", "status_reply",
         "help_reply", "test_message", "mvd_escalation_1", "mvd_escalation_2",
         "task_added", "task_completed", "task_list"}
    for name in sorted(unused):
        warnings.append(f"template {name!r} is defined but never used.")

    now = tu.now_local(cfg.tz)
    for deadline in cfg.deadlines:
        if deadline.is_open and deadline.due < now:
            warnings.append(
                f"deadline {deadline.id!r} ({deadline.course}) is past due and still marked pending."
            )

    if warnings:
        print("\nWARNINGS (not fatal)")
        for warning in warnings:
            print(f"  - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
