# Human OS

A Telegram bot that runs a college schedule for a brain that does not run on
reminders alone. It sends a morning briefing, transition warnings before every
class and shift, pre-emptive food prompts before the evening crash, a hard stop
on late-night music sessions, and a nightly check-in that escalates if it is
ignored.

It runs in the cloud on a schedule, so it works while your laptop is asleep,
closed, or out of battery.

---

## What it actually does

| Time | What arrives |
|---|---|
| 06:30 | Morning briefing: date, MVD checklist, today's schedule, top 3, deadlines |
| 15 min before each class/shift | Transition prompt with the building and room |
| After class | Definition-of-Done prompt: 5 minutes of notes, then move |
| 12:15 and 15:45 | Food, before the medication crash decides for you |
| 19:45 / 20:00 | Music hard-stop warning, then enforcement |
| 21:00 | MVD check-in — reply `done` or `skip`, or it escalates twice |
| 22:30 | Shutdown routine: phone out of the bedroom, tomorrow staged |

Miss a day and the next morning's briefing opens with a restart protocol
instead of the normal one. Nothing accumulates, nothing shames.

**Minimum Viable Day:** shower, class, 2–3 real meals. Everything else is
optional on a bad day.

---

## How it is built

```
config/schedule.yaml    every recurring event, by weekday
config/deadlines.yaml   academic due dates and what "done" means
config/messages.yaml    message templates and tone rules
src/main.py             the runner: what is due that has not been sent?
src/telegram_sender.py  Telegram API with retries and rate-limit handling
src/state_store.py      de-duplication, check-in state, streaks
src/time_utils.py       timezone and the late-delivery grace window
scripts/                bootstrap, validate, test send
state/state.json        written by the bot; also a log of your own days
```

The design point: the runner is **stateless per invocation**. GitHub Actions
wakes it every 5 minutes, it asks "what should have been said by now that has
not been said yet?", sends that, records it, and exits. A late run, a retried
run, or two overlapping runs can never double-send.

**Why not the `schedule` library?** The original version used a blocking
`while True` loop. That needs a process alive 24/7. A cron runner gives you a
14-minute container, so a 06:30 reminder would only fire if the container
happened to be alive at 06:30 — which it almost never is.

### Why public

GitHub gives unlimited Actions minutes to public repos and 2,000 a month to
private ones. A 5-minute cron uses roughly 6,000. Making the repo public keeps
it free at 5-minute precision; your token and chat id live in GitHub Secrets,
never in the code. The tradeoff is that your class schedule is visible to
anyone who finds the repo.

Prefer private? Change the cron in `.github/workflows/human_os_bot.yml` to
`'*/15 10-23 * * *'` and raise `grace_minutes` in `config/schedule.yaml` to 20.
Reminders then land up to 15 minutes late, which is fine for food prompts and
poor for "leave now" prompts.

---

## Setup

Manual steps are in **[MANUAL_ACTIONS.md](MANUAL_ACTIONS.md)** — start there.

### 1. Telegram

Message **@BotFather** → `/newbot` (or `/mybots` → *Revoke current token* if you
are replacing a leaked one). Then message your bot once and open
`https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id.

### 2. Local

```powershell
cd "C:\Users\Josco\human-os-bot"
python scripts\bootstrap_project.py
```

That creates missing folders, copies `.env.example` to `.env`, installs
dependencies, and validates the config. Then open `.env` and fill in your token
and chat id.

> **Paths with spaces need quotes.** `cd C:\Users\Josco\OneDrive\Desktop\AI assistant`
> fails; `cd "C:\Users\Josco\OneDrive\Desktop\AI assistant"` works. Same for
> `python "C:\path with spaces\script.py"`.

### 3. Deploy

Upload the **contents** of this folder to a GitHub repo — not the folder
itself. `.github/workflows/` must sit at the repo root or Actions will ignore
it. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` under
**Settings → Secrets and variables → Actions**.

---

## Everyday commands

```powershell
python scripts\test_send.py                      # prove delivery works right now
python scripts\validate_config.py                # check YAML before it breaks a morning
python -m src.main --list                        # what fires today
python -m src.main --dry-run                     # render without sending
python -m src.main --dry-run --at "2026-09-16 06:31"   # preview any moment
python -m src.main --only mon_briefing --force   # send one specific message now
python -m src.main                               # a real run
```

From the Actions tab, **Run workflow** offers the same modes: `run`, `test`,
`dry-run`, `list`, plus a single event id and a simulated time.

### Talking to the bot

| You send | It does |
|---|---|
| `+ buy stamps` | adds a task (`/add buy stamps` works too) |
| `+ !housing form @fri` | `!` marks it top priority, `@fri` sets a due date |
| `/tasks` | shows the numbered list |
| `/done 3` | closes task 3 |
| `/drop 3` | deletes task 3 — deciding not to do it is a decision |
| `done` | closes the open check-in, increments your streak |
| `skip` | logs it as missed, no spiral, triggers tomorrow's restart protocol |
| `/status` | today's messages, task counts, open check-ins, streak |
| `/help` | the list above |

### The task list

Capture has to cost nothing, so adding a task is one line of text to the bot
from wherever you are. Due dates accept `@today`, `@tomorrow`, a weekday like
`@fri`, or `@2026-09-20`. Both suffixes are optional.

Tasks then feed the rest of the system rather than sitting in a separate app:

- The **morning briefing** lists your open tasks, and anything starred, due
  today, or older than 4 days competes for a slot in the Top 3 alongside
  academic deadlines.
- The **shutdown message** shows what is still open while tomorrow can still
  absorb it, and counts what you closed.
- Tasks open 4+ days get an age marker; at 8+ days the list says *shrink it or
  drop it*. An old task is usually badly defined, not evidence about you.

The list lives in `state/tasks.json`, committed by the workflow, so it survives
runs and is readable as plain text. You can also manage it at the keyboard:

```powershell
python scripts\task.py                          # show the list
python scripts\task.py add "mail the form @fri"
python scripts\task.py done 3
```

If you add tasks locally, commit and push `state/tasks.json` so the cloud
runner sees them — texting the bot avoids that entirely.

---

## Editing your schedule

Everything lives in `config/schedule.yaml`. Times are `"HH:MM"` in
America/New_York. Each event needs a unique `id`, a `time`, and a `template`
that exists in `messages.yaml`.

```yaml
- id: tue_gym
  time: "17:00"
  template: transition
  vars:
    minutes: 15
    what: "gym"
    where: "the rec center"
    extra: "Bag is already packed."
```

`variant:` gates an event behind a switch in `active_variants` — that is how
the current work schedule and the proposed post-boss-conversation schedule both
live in one file without conflicting.

Always run `python scripts\validate_config.py` afterwards. It catches unknown
templates, duplicate ids, bad times, and two messages firing in the same minute.

---

## Troubleshooting

**`can't open file '.../bot.py': [Errno 2] No such file or directory`**
The repo has the project inside a subfolder. `bot.py`, `src/` and `.github/`
must be at the repo root. Re-upload the *contents*, not the folder.

**Workflow is green but nothing arrives.**
Open the run log. Either "Quiet hours", "0 event(s) due" (nothing was
scheduled), or a Telegram error. The old workflow ended with `|| true`, which
made failures look like successes — that is gone.

**401 from Telegram.** The token is wrong or was revoked. Regenerate with
BotFather and update the GitHub Secret.

**Messages arrive 10–20 minutes late.** GitHub's cron is best-effort and queues
under load. The `grace_minutes` window means a late runner still delivers
rather than skipping. If exact timing matters more than free hosting, run the
same command from Windows Task Scheduler as a backup:
```powershell
schtasks /create /tn "HumanOS" /tr "python C:\Users\Josco\human-os-bot\bot.py" /sc minute /mo 5
```
Only do this if the cloud runner is off, or both will send.

**A message repeated.** The state commit failed on the previous run — check for
a `::warning::` in the log. Harmless, self-corrects.

**Emoji or `?` characters in the PowerShell output.** Cosmetic console encoding
only; Telegram receives the real text.

---

## Boundaries

This bot handles scheduling, reminders, planning and accountability. It does
not write, solve, or draft any graded coursework — your syllabi restrict
generative AI, and that line is not worth crossing for a homework set that gets
dropped anyway.

Accountability here means clear prompts, forced check-ins, and escalation.
Never shame, never financial or social consequences.
