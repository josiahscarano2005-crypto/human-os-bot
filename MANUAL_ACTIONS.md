# Manual Actions

Only you can do these. The code is already written and tested.
Work top to bottom. Stop after Block 1 if your brain is done for the night —
Block 1 alone gets the system live.

---

## BLOCK 1 — Get it running (about 20 minutes)

- [ ] **1. Revoke the old Telegram token.**
      Open Telegram → message **@BotFather** → `/mybots` → pick your bot →
      *API Token* → **Revoke current token**.
      The old token was pasted into a chat, which means it is public. Anyone
      holding it can message you as your bot. Revoking it takes 15 seconds.

- [ ] **2. Copy the new token.** BotFather shows it immediately after revoking.
      Do not paste it into any chat window, including an AI chat.

- [ ] **3. Get your chat id.** Message your bot once (any text), then open in a browser:
      `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
      Find `"chat":{"id":123456789` — that number is your chat id.

- [ ] **4. Fill in `.env` locally.**
      Open `C:\Users\Josco\human-os-bot\.env` and replace the two placeholders.
      This file is gitignored and never leaves your machine.

- [ ] **5. Prove delivery works.** In PowerShell:
      ```powershell
      cd "C:\Users\Josco\human-os-bot"
      python scripts\test_send.py
      ```
      A message should hit your phone within seconds. If it does not, see
      Troubleshooting in `README.md`.

- [ ] **6. Create the GitHub repository.**
      On github.com click **New repository**, name it `human-os-bot`.
      **Make it PUBLIC.** Reason in `README.md` under "Why public" — short
      version: private repos only get 2,000 free Actions minutes a month and
      this bot needs roughly 6,000. Your token lives in Secrets, not in the
      code, so a public repo does not expose it.
      If you would rather keep it private, say so and the cron gets widened to
      15 minutes to fit the free tier.

- [ ] **7. Delete the old broken files from that repo** if you are reusing an
      existing one. The old `bot.py` and old workflow must not survive.

- [ ] **8. Upload this project.** In the repo: **Add file → Upload files**, then
      drag the **contents** of `human-os-bot` (the `src`, `config`, `scripts`,
      `.github`, `state` folders and the loose files) — **not** the folder
      itself. If the repo ends up with `human-os-bot/.github/...`, Actions will
      never run, because it only reads `.github/workflows` at the top level.

- [ ] **9. Add the two secrets.**
      Repo → **Settings → Secrets and variables → Actions → New repository secret**:
      - `TELEGRAM_BOT_TOKEN` — your new token
      - `TELEGRAM_CHAT_ID` — your chat id

- [ ] **10. Enable and test Actions.**
      **Actions** tab → enable workflows if prompted → **Human OS Bot** →
      **Run workflow** → set *What to run* to `test` → **Run workflow**.
      A test message should arrive. That is the whole system proven end to end.

---

## BLOCK 2 — Real-world decisions (this week)

- [ ] **11. Talk to your boss.** Ask for two things:
      Tuesday's 10–4 shift moved to **Thursday 10–4**, and Friday capped at
      **5 PM**. Reason to give: Tuesday conflicts with a required lab.
      When it is agreed, edit `config/schedule.yaml` → `active_variants`:
      ```yaml
      active_variants:
        - no_work_tuesday
        - work_thursday
        - zool_lab_l04
        - ansc_r04
      ```
      (Remove `work_tuesday`.) Nothing else needs to change.

- [ ] **12. Confirm your ZOOL lab section.** The schedule assumes **L04,
      Wednesday 1:10–3:30, Rudman G31**. If you get a different section,
      change the `variant` lines in `config/schedule.yaml`.

- [ ] **13. Confirm your ANSC recitation.** The schedule assumes **R04,
      Wednesday 12:10–1:00**. Two syllabi disagree on the room — one says
      Ham-Smith 126, one says Pettee HS126. Ask once, then fix the `where:`
      line under `wed_ansc_recit_transition`.

- [ ] **14. Email the ZOOL professor about Quiz #1** (was due 9/14, 8:00 AM).
      You registered late. That is a legitimate reason and the ask is small.
      One sentence: late registration, requesting the late window.

- [ ] **15. Let BMCB Homework 1 go.** Two lowest scores are dropped; it is
      already marked `accepted_drop` in `config/deadlines.yaml`. It will stop
      appearing in your briefings. Do not spend a single further minute on it.

---

## BLOCK 3 — Physical environment (do tonight, takes 10 minutes)

These do more work than any code in this repo.

- [ ] **16. Phone charger moves out of the bedroom.** Tonight. The morning
      briefing cannot beat a phone that is within arm's reach at 6:30 AM.
- [ ] **17. Shoes, bag and water bottle staged at the door** before bed.
- [ ] **18. Closed-toe shoes set aside for Wednesdays** — lab requires them and
      you will not come home between 8:10 AM and 3:30 PM.
- [ ] **19. Food staged for Wednesday.** Two portable meals in the bag. Your
      Wednesday has no real eating window and that is exactly how the 6 PM
      crash gets built.
- [ ] **20. Optional: a vibrating watch or band.** Anything that buzzes on the
      wrist beats a phone notification you have learned to swipe away.
      A cheap fitness band paired to Telegram notifications is enough.

---

## Ongoing, about 5 minutes a week

- [ ] Add new deadlines to `config/deadlines.yaml` as syllabi update.
      Mark things `status: submitted` when they close out.
- [ ] Reply **done** or **skip** to the 9 PM check-in. That single word is what
      drives the streak counter and the next-morning recovery protocol.
- [ ] Text the bot `+ <task>` the moment something lands on you — a form, an
      errand, a reply you owe someone. It appears in tomorrow's briefing.
      Do not keep a second list somewhere else; two lists means no list.
- [ ] Run `python scripts\validate_config.py` after editing any YAML.
