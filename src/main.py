"""Human OS runner.

Designed to be executed by a cron-style scheduler (GitHub Actions) every few
minutes. Each run answers one question: what should have been said by now that
has not been said yet? State on disk makes that idempotent, so a late run, a
retried run, or two overlapping runs never double-send.

  python -m src.main                     # normal run
  python -m src.main --dry-run           # print, never send
  python -m src.main --test              # send one test message
  python -m src.main --list              # show today's schedule
  python -m src.main --only mon_briefing --force
  python -m src.main --at "2026-09-14 06:31"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import time_utils as tu  # noqa: E402
from src.config_loader import Config, ConfigError, Deadline, Event, load_config  # noqa: E402
from src.state_store import StateStore  # noqa: E402
from src.task_store import TaskStore  # noqa: E402
from src.telegram_sender import TelegramError, TelegramSender  # noqa: E402

CONFIG_DIR = ROOT / "config"
STATE_PATH = ROOT / "state" / "state.json"
TASKS_PATH = ROOT / "state" / "tasks.json"

DONE_WORDS = {"done", "d", "yes", "y", "did it", "complete", "completed", "✅", "👍"}
SKIP_WORDS = {"skip", "no", "n", "missed", "nope", "failed"}

log = logging.getLogger("human_os")


class DueEvent:
    """An event whose time has arrived, real or synthesised from a deadline."""

    def __init__(self, event: Event, scheduled: datetime, day_iso: str, extra_vars: dict[str, Any] | None = None):
        self.event = event
        self.scheduled = scheduled
        self.day_iso = day_iso
        self.extra_vars = extra_vars or {}

    @property
    def key(self) -> str:
        return f"{self.day_iso}:{self.event.id}"


# --------------------------------------------------------------------------
# Message content
# --------------------------------------------------------------------------


def open_deadlines(cfg: Config, now: datetime) -> list[Deadline]:
    return [d for d in cfg.deadlines if d.is_open]


def briefing_vars(cfg: Config, state: StateStore, now: datetime, tasks: TaskStore | None = None) -> dict[str, Any]:
    day = cfg.day(tu.weekday_key(now))
    policy = cfg.reminder_policy
    lookahead = int(policy.get("briefing_lookahead_days", 5))
    nag_days = int(policy.get("overdue_nag_days", 7))

    agenda = "\n".join(day.agenda) if day.agenda else "Nothing fixed. Pick one thing and start it."

    upcoming: list[str] = []
    overdue: list[str] = []
    for deadline in open_deadlines(cfg, now):
        days_out = (deadline.due.date() - now.date()).days
        line = f"{deadline.course}: {deadline.title} - {tu.describe_delta(deadline.due, now)}"
        if days_out < 0:
            if abs(days_out) <= nag_days:
                overdue.append(line)
        elif days_out <= lookahead:
            upcoming.append(line)

    # Top 3 carries the urgent items; the block below only shows what did not
    # fit, so the briefing never says the same thing twice. Your own starred,
    # due or stale tasks outrank a deadline that is still days away.
    task_picks = tasks.top_for_briefing(now.date()) if tasks else []
    ranked = overdue + task_picks + upcoming
    priorities = ranked[:3]
    leftover = [line for line in ranked[3:] if line not in task_picks]

    deadline_block = ""
    if overdue:
        deadline_block += "\n*Overdue - handle it or formally let it go*\n" + "\n".join(
            f"- {line}" for line in overdue
        ) + "\n"
    if leftover:
        deadline_block += "\n*Also this week*\n" + "\n".join(f"- {line}" for line in leftover) + "\n"

    while len(priorities) < 3:
        priorities.append(
            ["Shower before anything else.", "Get to class on time.", "Eat a real meal before noon."][
                len(priorities)
            ]
        )
    priority_text = "\n".join(f"{i}. {p}" for i, p in enumerate(priorities, start=1))

    yesterday = (now - timedelta(days=1)).date().isoformat()
    recovery = ""
    if state.day_status(yesterday) in {"skip", "no_response"}:
        recovery = cfg.render("recovery_block") + "\n"

    task_block = ""
    if tasks:
        open_tasks = tasks.open_tasks(now.date())
        if open_tasks:
            task_block = "\n*Your list* (/tasks, /done <n>)\n" + tasks.render_list(
                now.date(), limit=5
            ) + "\n"

    return {
        "date": tu.fmt_date(now),
        "agenda": agenda,
        "priorities": priority_text,
        "deadlines": deadline_block,
        "recovery": recovery,
        "tasks": task_block,
    }


def render_event(
    cfg: Config, state: StateStore, due: DueEvent, now: datetime, tasks: TaskStore | None = None
) -> str:
    values: dict[str, Any] = dict(due.event.vars)
    values.update(due.extra_vars)
    if due.event.template in {"morning_briefing", "weekend_briefing"}:
        values.update(briefing_vars(cfg, state, now, tasks))
    elif due.event.template == "shutdown" and tasks:
        # The last message of the day is the right moment to close the loop on
        # anything still open, while tomorrow can still absorb it.
        open_tasks = tasks.open_tasks(now.date())
        values["open_tasks"] = (
            "\n*Still open*\n" + tasks.render_list(now.date(), limit=4) + "\n" if open_tasks else ""
        )
        done_today = tasks.completed_on(now.date())
        if done_today:
            values["closed_today"] = f"Closed today: {len(done_today)}. That counts.\n"
    return cfg.render(due.event.template, **values)


# --------------------------------------------------------------------------
# What is due
# --------------------------------------------------------------------------


def deadline_events(cfg: Config, now: datetime) -> list[DueEvent]:
    alert_times = (cfg.reminder_policy.get("alert_times") or {})
    results: list[DueEvent] = []
    for deadline in open_deadlines(cfg, now):
        for offset in deadline.remind_days_before:
            alert_day = deadline.due.date() - timedelta(days=int(offset))
            hhmm = str(alert_times.get(str(offset), "17:00"))
            scheduled = tu.combine(alert_day, hhmm, cfg.tz)
            if scheduled > deadline.due:
                continue
            event = Event(
                id=f"deadline_{deadline.id}_d{offset}",
                time=hhmm,
                template="deadline_alert",
                vars={
                    "course": deadline.course,
                    "title": deadline.title,
                    "due_str": tu.fmt_datetime(deadline.due),
                    "when": tu.describe_delta(deadline.due, scheduled),
                    "dod": deadline.dod,
                },
            )
            results.append(DueEvent(event, scheduled, alert_day.isoformat()))
    return results


def collect_candidates(cfg: Config, now: datetime) -> list[DueEvent]:
    """Today's events plus yesterday's, so a late-night run still catches up."""
    candidates: list[DueEvent] = []
    for offset in (0, -1):
        day = (now + timedelta(days=offset)).date()
        for event in cfg.day(tu.weekday_key(day)).events:
            if not cfg.variant_active(event.variant):
                continue
            candidates.append(DueEvent(event, tu.combine(day, event.time, cfg.tz), day.isoformat()))
    candidates.extend(deadline_events(cfg, now))
    return sorted(candidates, key=lambda c: c.scheduled)


def select_due(cfg: Config, state: StateStore, now: datetime, args: argparse.Namespace) -> list[DueEvent]:
    grace = args.grace if args.grace is not None else cfg.grace_minutes
    selected: list[DueEvent] = []
    for candidate in collect_candidates(cfg, now):
        if args.only and candidate.event.id != args.only:
            continue
        if args.force and args.only:
            selected.append(candidate)
            continue
        window = candidate.event.grace_minutes or grace
        if not tu.is_due(candidate.scheduled, now, window):
            continue
        if state.was_sent(candidate.key) and not args.force:
            continue
        selected.append(candidate)
    return selected


# --------------------------------------------------------------------------
# Replies and escalation
# --------------------------------------------------------------------------


def handle_task_command(
    cfg: Config, tasks: TaskStore, sender: TelegramSender, now: datetime, raw: str
) -> bool:
    """Handle task commands. Returns True when the message was a task command."""
    today = now.date()
    lowered = raw.strip().lower()

    if raw.startswith("+") or lowered.startswith(("/add ", "/add\n")):
        body = raw[1:] if raw.startswith("+") else raw[5:]
        if not body.strip():
            sender.send("Send the task with it, e.g. `+ mail the housing form @fri`")
            return True
        task = tasks.add(body, now)
        sender.send(cfg.render("task_added", task=task.label(today), count=len(tasks.open_tasks(today))))
        return True

    if lowered.startswith(("/tasks", "/list")):
        sender.send(cfg.render("task_list", tasks=tasks.render_list(today)))
        return True

    if lowered.startswith(("/done ", "/drop ")):
        command, _, argument = lowered.partition(" ")
        try:
            task_id = int(argument.strip().lstrip("#"))
        except ValueError:
            sender.send(f"Use a number from /tasks, e.g. `{command} 3`")
            return True
        task = tasks.complete(task_id, now) if command == "/done" else tasks.drop(task_id)
        if not task:
            sender.send(f"No open task {task_id}. Send /tasks to see the list.")
        elif command == "/done":
            remaining = len(tasks.open_tasks(today))
            sender.send(cfg.render("task_completed", task=task.text, count=remaining))
        else:
            sender.send(f"Dropped: {task.text}\nDeciding not to do it is a decision. Fine.")
        return True

    return False


def poll_replies(
    cfg: Config, state: StateStore, sender: TelegramSender, now: datetime, tasks: TaskStore | None = None
) -> None:
    updates = sender.get_updates(offset=state.telegram_offset)
    if not updates:
        return
    highest = state.telegram_offset or 0
    for update in updates:
        highest = max(highest, int(update.get("update_id", 0)) + 1)
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != sender.chat_id:
            continue
        raw = str(message.get("text", "")).strip()
        text = raw.lower()
        if not text:
            continue
        today = now.date().isoformat()

        if tasks is not None and handle_task_command(cfg, tasks, sender, now, raw):
            continue

        if text in DONE_WORDS:
            resolved = state.resolve_acks("done", now)
            if not resolved:
                state.log_day(today, "manual_mvd", "done", now)
            streak = state.streak(today)
            streak_text = f"{streak + 1} days straight." if streak else ""
            sender.send(cfg.render("ack_ok", streak=streak_text))
            log.info("Acknowledged %s open check-in(s) as done.", len(resolved))
        elif text in SKIP_WORDS:
            resolved = state.resolve_acks("skip", now)
            if not resolved:
                state.log_day(today, "manual_mvd", "skip", now)
            sender.send(cfg.render("ack_skip"))
            log.info("Marked %s open check-in(s) as skipped.", len(resolved))
        elif text.startswith("/status"):
            sender.send(
                cfg.render(
                    "status_reply", date=tu.fmt_date(now), status_body=status_body(state, now, tasks)
                )
            )
        elif text.startswith(("/help", "/start")):
            sender.send(cfg.render("help_reply"))

    state.telegram_offset = highest


def status_body(state: StateStore, now: datetime, tasks: TaskStore | None = None) -> str:
    today = now.date().isoformat()
    sent = state.sent_today(today)
    pending = state.pending_acks
    lines = [f"Messages sent today: {len(sent)}"]
    if tasks is not None:
        open_count = len(tasks.open_tasks(now.date()))
        closed = len(tasks.completed_on(now.date()))
        lines.append(f"Tasks open: {open_count}, closed today: {closed}")
    if pending:
        lines.append("Open check-ins: " + ", ".join(item["event_id"] for item in pending))
        lines.append("Reply *done* or *skip*.")
    else:
        lines.append("No open check-ins.")
    streak = state.streak(today)
    lines.append(f"MVD streak going into today: {streak} day(s).")
    yesterday = (now - timedelta(days=1)).date().isoformat()
    lines.append(f"Yesterday: {state.day_status(yesterday) or 'no data'}")
    return "\n".join(lines)


def run_escalations(cfg: Config, state: StateStore, sender: TelegramSender, now: datetime) -> int:
    sent = 0
    policy = cfg.ack
    for item in list(state.pending_acks):
        asked_at = datetime.fromisoformat(item["asked_at"])
        already = int(item.get("escalations_sent", 0))
        if already >= policy.max_escalations:
            continue
        due_at = asked_at + timedelta(minutes=policy.escalate_after_minutes * (already + 1))
        if now < due_at:
            continue
        template = policy.escalation_templates[min(already, len(policy.escalation_templates) - 1)]
        sender.send(cfg.render(template))
        item["escalations_sent"] = already + 1
        sent += 1
        log.info("Escalation %s sent for %s", already + 1, item["event_id"])
    expired = state.expire_acks(now, policy.expire_after_minutes)
    for item in expired:
        log.info("Check-in %s expired with no response.", item["event_id"])
    return sent


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_sender(args: argparse.Namespace) -> TelegramSender:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    dry = args.dry_run or os.getenv("HUMAN_OS_DRY_RUN", "0") == "1"
    return TelegramSender(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        dry_run=dry,
    )


def cmd_doctor() -> int:
    """Diagnose credential problems in whichever environment this is running in."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    from src.telegram_sender import describe_token, sanitize_secret

    raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    raw_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    token = sanitize_secret(raw_token, "TELEGRAM_BOT_TOKEN")
    chat_id = sanitize_secret(raw_chat, "TELEGRAM_CHAT_ID")

    print("Credential check (no secret values are printed)")
    print(f"  token present      {'yes' if raw_token else 'NO'}")
    print(f"  token shape        {describe_token(token)}")
    if raw_token != token:
        print("  token cleanup      stripped quotes/whitespace/name prefix from the stored value")
    print(f"  chat id present    {'yes' if raw_chat else 'NO'}")
    print(f"  chat id numeric    {'yes' if chat_id.lstrip('-').isdigit() else 'NO - this should be a number'}")
    if raw_chat != chat_id:
        print("  chat id cleanup    stripped quotes/whitespace/name prefix from the stored value")

    if not token or not chat_id:
        print("\nFAIL: a credential is missing.")
        return 2

    import requests

    try:
        response = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    except requests.RequestException as exc:
        print(f"\nFAIL: could not reach Telegram: {exc}")
        return 2

    if response.status_code == 200:
        bot = (response.json().get("result") or {})
        print(f"\nOK: token is valid. Bot is @{bot.get('username')} (id {bot.get('id')}).")
        return 0

    print(f"\nFAIL: Telegram returned {response.status_code} for getMe.")
    print("  404 or 401 means the token value itself is wrong, truncated, or revoked.")
    print("  Re-copy it from @BotFather and paste ONLY the token - no name, quotes or spaces.")
    return 2


def cmd_list(cfg: Config, now: datetime) -> int:
    day = cfg.day(tu.weekday_key(now))
    print(f"{tu.fmt_date(now)} ({cfg.timezone})")
    print(f"Active variants: {', '.join(sorted(cfg.active_variants)) or 'none'}\n")
    rows = [(e.time, e.id, e.template, "ack" if e.requires_ack else "") for e in day.events if cfg.variant_active(e.variant)]
    rows += [
        (d.scheduled.strftime("%H:%M"), d.event.id, d.event.template, "")
        for d in deadline_events(cfg, now)
        if d.day_iso == now.date().isoformat()
    ]
    for time_str, event_id, template, flag in sorted(rows):
        print(f"  {time_str}  {event_id:<28} {template:<20} {flag}")
    if not rows:
        print("  (nothing scheduled)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Human OS - ADHD support bot runner")
    parser.add_argument("--dry-run", action="store_true", help="render messages but never send")
    parser.add_argument("--at", help='simulate a local time, e.g. "2026-09-14 06:31"')
    parser.add_argument("--only", help="run a single event id")
    parser.add_argument("--force", action="store_true", help="ignore de-duplication (use with --only)")
    parser.add_argument("--grace", type=int, help="override the late-delivery window in minutes")
    parser.add_argument("--test", action="store_true", help="send one test message and exit")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="send for real but record nothing, so the scheduled copy still arrives",
    )
    parser.add_argument("--list", action="store_true", help="print today's schedule and exit")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="check credentials and report what is wrong, without printing them",
    )
    parser.add_argument("--no-poll", action="store_true", help="skip reading replies")
    parser.add_argument("--ignore-quiet-hours", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.doctor:
        return cmd_doctor()

    try:
        cfg = load_config(CONFIG_DIR)
    except ConfigError as exc:
        log.error("Configuration is invalid:\n%s", exc)
        return 2

    now = tu.now_local(cfg.tz, args.at)

    if args.list:
        return cmd_list(cfg, now)

    try:
        sender = build_sender(args)
    except TelegramError as exc:
        log.error("%s", exc)
        return 2

    state = StateStore(STATE_PATH)
    state.touch(now)
    tasks = TaskStore(TASKS_PATH)

    if args.test:
        sender.send(cfg.render("test_message", now=tu.fmt_datetime(now), mode="dry-run" if sender.dry_run else "live"))
        log.info("Test message dispatched.")
        return 0

    quiet = tu.in_quiet_hours(now, cfg.quiet_hours_start, cfg.quiet_hours_end)
    if quiet and not (args.ignore_quiet_hours or args.only):
        log.info("Quiet hours (%s-%s). Nothing sent.", cfg.quiet_hours_start, cfg.quiet_hours_end)
        if not sender.dry_run:
            state.save()
        return 0

    if not args.no_poll and not sender.dry_run:
        try:
            poll_replies(cfg, state, sender, now, tasks)
        except TelegramError as exc:
            log.warning("Reply polling failed, continuing: %s", exc)

    # Preview sends the real message but leaves no trace, so the scheduled
    # delivery is never consumed by looking at it early.
    record = not (sender.dry_run or args.preview)
    if args.preview:
        args.force = True

    due = select_due(cfg, state, now, args)
    log.info("%s event(s) due at %s.", len(due), now.isoformat(timespec="minutes"))

    failures = 0
    for item in due:
        try:
            text = render_event(cfg, state, item, now, tasks)
        except KeyError as exc:
            log.error("Skipping %s: %s", item.event.id, exc)
            failures += 1
            continue
        try:
            sender.send(text)
        except TelegramError as exc:
            log.error("Failed to send %s: %s", item.event.id, exc)
            failures += 1
            continue
        log.info("Sent %s (scheduled %s)", item.event.id, item.scheduled.strftime("%H:%M"))
        if record:
            state.mark_sent(item.key, now, item.event.id, item.event.template)
            if item.event.requires_ack:
                state.add_pending_ack(item.key, item.event.id, now, item.day_iso)

    if record:
        try:
            run_escalations(cfg, state, sender, now)
        except TelegramError as exc:
            log.warning("Escalation failed: %s", exc)

    # Tasks are your data, not scheduling bookkeeping, so they are saved even
    # in preview mode - a task you added must never be silently discarded.
    if not sender.dry_run:
        tasks.prune(now)
        if tasks.save():
            log.info("Task list updated at %s", TASKS_PATH)

    if args.preview:
        log.info("Preview mode: nothing recorded, the scheduled copy will still arrive.")
        return 1 if failures else 0

    state.prune(now)
    if not sender.dry_run and state.save():
        log.info("State updated at %s", STATE_PATH)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
