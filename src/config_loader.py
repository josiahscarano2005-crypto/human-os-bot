"""Load and validate the YAML configuration.

A bad edit to schedule.yaml should fail loudly at validation time, never
silently at 6:30 AM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import time_utils as tu


class ConfigError(Exception):
    """Raised with every problem found, not just the first one."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(f"  - {p}" for p in problems))


@dataclass
class Event:
    id: str
    time: str
    template: str
    vars: dict[str, Any] = field(default_factory=dict)
    variant: str | None = None
    requires_ack: bool = False
    grace_minutes: int | None = None


@dataclass
class Day:
    key: str
    agenda: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


@dataclass
class Deadline:
    id: str
    course: str
    title: str
    due: datetime
    dod: str = ""
    status: str = "pending"
    remind_days_before: list[int] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status == "pending"


@dataclass
class AckPolicy:
    escalate_after_minutes: int = 25
    max_escalations: int = 2
    expire_after_minutes: int = 150
    escalation_templates: list[str] = field(
        default_factory=lambda: ["mvd_escalation_1", "mvd_escalation_2"]
    )


@dataclass
class Config:
    timezone: str
    tz: Any
    active_variants: set[str]
    grace_minutes: int
    quiet_hours_start: str | None
    quiet_hours_end: str | None
    ack: AckPolicy
    days: dict[str, Day]
    templates: dict[str, str]
    tone: dict[str, Any]
    deadlines: list[Deadline]
    reminder_policy: dict[str, Any]

    def variant_active(self, variant: str | None) -> bool:
        return variant is None or variant in self.active_variants

    def day(self, key: str) -> Day:
        return self.days.get(key, Day(key=key))

    def render(self, template_key: str, **values: Any) -> str:
        template = self.templates.get(template_key)
        if template is None:
            raise KeyError(f"Unknown message template: {template_key}")
        rendered = template.format_map(_Blanks(values))
        # Placeholders that resolve to nothing leave ragged whitespace behind.
        lines: list[str] = []
        for line in rendered.splitlines():
            line = line.rstrip()
            if not line and lines and not lines[-1]:
                continue
            lines.append(line)
        return "\n".join(lines).strip()


class _Blanks(dict):
    """Missing placeholders render as empty strings instead of crashing."""

    def __missing__(self, key: str) -> str:
        return ""


def _read_yaml(path: Path, problems: list[str]) -> dict[str, Any]:
    if not path.exists():
        problems.append(f"Missing config file: {path}")
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        problems.append(f"{path.name} is not valid YAML: {exc}")
        return {}
    if not isinstance(data, dict):
        problems.append(f"{path.name} must contain a mapping at the top level.")
        return {}
    return data


def load_config(config_dir: Path) -> Config:
    problems: list[str] = []

    schedule = _read_yaml(config_dir / "schedule.yaml", problems)
    messages = _read_yaml(config_dir / "messages.yaml", problems)
    deadlines_doc = _read_yaml(config_dir / "deadlines.yaml", problems)
    if problems:
        raise ConfigError(problems)

    tz_name = schedule.get("timezone", "America/New_York")
    try:
        tz = tu.get_tz(tz_name)
    except Exception as exc:
        raise ConfigError([f"Unknown timezone {tz_name!r}: {exc}"]) from exc

    templates = messages.get("templates") or {}
    if not isinstance(templates, dict) or not templates:
        problems.append("messages.yaml must define a non-empty `templates` mapping.")

    defaults = schedule.get("defaults") or {}
    ack_raw = defaults.get("ack") or {}
    ack = AckPolicy(
        escalate_after_minutes=int(ack_raw.get("escalate_after_minutes", 25)),
        max_escalations=int(ack_raw.get("max_escalations", 2)),
        expire_after_minutes=int(ack_raw.get("expire_after_minutes", 150)),
        escalation_templates=list(
            ack_raw.get("escalation_templates") or ["mvd_escalation_1", "mvd_escalation_2"]
        ),
    )

    known_variants = set((schedule.get("variants") or {}).keys())
    active_variants = set(schedule.get("active_variants") or [])
    for variant in sorted(active_variants - known_variants):
        problems.append(f"active_variants lists {variant!r}, which is not declared under `variants`.")

    days: dict[str, Day] = {}
    seen_ids: set[str] = set()
    raw_days = schedule.get("days") or {}
    for day_key in tu.WEEKDAY_KEYS:
        raw_day = raw_days.get(day_key) or {}
        events: list[Event] = []
        for index, raw_event in enumerate(raw_day.get("events") or []):
            where = f"{day_key}[{index}]"
            event_id = raw_event.get("id")
            if not event_id:
                problems.append(f"{where}: event is missing `id`.")
                continue
            if event_id in seen_ids:
                problems.append(f"{where}: duplicate event id {event_id!r}. Ids must be unique.")
            seen_ids.add(event_id)

            event_time = raw_event.get("time")
            try:
                tu.parse_hhmm(str(event_time))
            except Exception:
                problems.append(f"{where} ({event_id}): bad time {event_time!r}, expected \"HH:MM\".")

            template = raw_event.get("template")
            if not template:
                problems.append(f"{where} ({event_id}): missing `template`.")
            elif templates and template not in templates:
                problems.append(
                    f"{where} ({event_id}): template {template!r} is not defined in messages.yaml."
                )

            variant = raw_event.get("variant")
            if variant and variant not in known_variants:
                problems.append(
                    f"{where} ({event_id}): variant {variant!r} is not declared under `variants`."
                )

            events.append(
                Event(
                    id=str(event_id),
                    time=str(event_time),
                    template=str(template),
                    vars=dict(raw_event.get("vars") or {}),
                    variant=variant,
                    requires_ack=bool(raw_event.get("requires_ack", False)),
                    grace_minutes=raw_event.get("grace_minutes"),
                )
            )
        days[day_key] = Day(
            key=day_key,
            agenda=[str(line) for line in (raw_day.get("agenda") or [])],
            events=events,
        )

    policy = deadlines_doc.get("reminder_policy") or {}
    default_offsets = [int(x) for x in (policy.get("default_remind_days_before") or [3, 1, 0])]

    deadlines: list[Deadline] = []
    deadline_ids: set[str] = set()
    for index, raw in enumerate(deadlines_doc.get("deadlines") or []):
        where = f"deadlines[{index}]"
        deadline_id = raw.get("id")
        if not deadline_id:
            problems.append(f"{where}: missing `id`.")
            continue
        if deadline_id in deadline_ids:
            problems.append(f"{where}: duplicate deadline id {deadline_id!r}.")
        deadline_ids.add(deadline_id)
        try:
            due = tu.parse_due(str(raw.get("due")), tz)
        except Exception:
            problems.append(
                f"{where} ({deadline_id}): bad due {raw.get('due')!r}, expected \"YYYY-MM-DD HH:MM\"."
            )
            continue
        status = str(raw.get("status", "pending"))
        if status not in {"pending", "submitted", "accepted_drop"}:
            problems.append(
                f"{where} ({deadline_id}): status {status!r} must be pending, submitted or accepted_drop."
            )
        deadlines.append(
            Deadline(
                id=str(deadline_id),
                course=str(raw.get("course", "")),
                title=str(raw.get("title", "")),
                due=due,
                dod=str(raw.get("dod", "")),
                status=status,
                remind_days_before=[int(x) for x in (raw.get("remind_days_before") or default_offsets)],
            )
        )

    for required in ("morning_briefing", "weekend_briefing", "test_message"):
        if templates and required not in templates:
            problems.append(f"messages.yaml is missing the required template {required!r}.")

    if problems:
        raise ConfigError(problems)

    return Config(
        timezone=tz_name,
        tz=tz,
        active_variants=active_variants,
        grace_minutes=int(defaults.get("grace_minutes", 40)),
        quiet_hours_start=defaults.get("quiet_hours_start"),
        quiet_hours_end=defaults.get("quiet_hours_end"),
        ack=ack,
        days=days,
        templates=templates,
        tone=messages.get("tone") or {},
        deadlines=sorted(deadlines, key=lambda d: d.due),
        reminder_policy=policy,
    )
