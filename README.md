# workdesk

A personal work dashboard for one engineer. It pulls your open work from Jira, GitHub, Slack and a manual
task list into one local page, ranks it by urgency and importance, suggests calendar time blocks and has a
pomodoro timer. Python 3.11+, standard library only, no build step, no LLM calls.

## Try it without any token

```
python3 -m workdesk demo          # rebuilds a demo database from fixtures and serves http://127.0.0.1:8765
python3 -m workdesk --demo today --no-sync
```

The demo uses canned API responses (invented names such as acme and MLP-123) shifted to today.

## Install

Nothing to install: run `python3 -m workdesk <command>` from the repository.
To get a `workdesk` command: `pip install -e .` (entry point `workdesk = workdesk.cli:main`), or add a shell
alias such as `alias workdesk='python3 -m workdesk'` with `PYTHONPATH` pointing at this directory.

## Setup

1. `workdesk init` writes `~/.config/workdesk/config.toml` (override with `WORKDESK_CONFIG`).
2. Edit it: set `enabled = true` for the sources you use. Every source is disabled by default.
3. Export tokens as environment variables. The config holds only the variable names (`token_env`).
4. `workdesk doctor` makes one cheap call per enabled source and prints `pass`, `FAIL` (with the reason: missing
   env var, 401 bad token, 403 or missing scope, network error) or `skip`.
5. `workdesk sync`, then `workdesk today`, or `workdesk serve` for the page.

The database lives at `~/.local/share/workdesk/workdesk.db` (override with `WORKDESK_DB` or `db_path`).

### Tokens and scopes

| Source | Variable | What to create | Scopes / access |
|---|---|---|---|
| Jira Cloud | `JIRA_TOKEN` | API token (id.atlassian.com, Security, API tokens). Set `email` and `base_url` in `[jira]`; `auth = "basic"`. For a bearer or PAT setup use `auth = "bearer"`. | Read access to issues you can see (Browse projects). |
| GitHub | `GITHUB_TOKEN` | Personal access token (classic: `repo` and `notifications`; fine-grained: Issues, Pull requests read and Metadata read, plus notifications access). | For GitHub Enterprise set `api_url = "https://ghe.example/api/v3"`. |
| Slack | `SLACK_TOKEN` | A **user** token (`xoxp-...`) from a Slack app installed to your workspace. | User scopes: `search:read`, `channels:history`, `groups:history`, `im:history`, `mpim:history`, `reactions:read`. Without a history scope the answered check fails and the item is simply kept. |
| Google Calendar (optional) | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client of type Desktop app in Google Cloud Console, Calendar API enabled. | Scope `https://www.googleapis.com/auth/calendar.events`. |

Google Calendar: set `[calendar] enabled = true`, export the two variables, then run `workdesk auth google`. It
opens a browser, receives the code on a loopback port on 127.0.0.1 (state check, PKCE) and stores the refresh
token in `~/.config/workdesk/google_token.json` with mode 600. Without the calendar the planner uses working
hours only and can export accepted blocks as an `.ics` file.

Tokens are never logged and never written to the config; error messages contain neither headers nor query strings.

## Daily use

```
workdesk today                 # at most 30 lines / 1800 characters; syncs first if the last sync is over 15 min old
workdesk show j1               # full detail of one item (aliases j=jira g=github s=slack m=manual, stable all day)
workdesk list                  # everything open, ranked
workdesk add "Write the report" --due 2026-01-20 --importance high --estimate 90
workdesk done j1               # also: snooze j1 1d|1w, set j1 importance|urgency high|medium|low|auto|0-100
workdesk set j1 estimate 120
workdesk plan                  # propose focus blocks for today (--day YYYY-MM-DD for another date)
workdesk plan --accept all     # accept the existing proposals (or give ids: --accept 3,4); creates calendar events
workdesk plan --accept all --ics blocks.ics
workdesk pomo start j1         # also: pomo stop, pomo status, pomo start --break
workdesk serve                 # the page on http://127.0.0.1:8765
```

Source of truth stays in Jira, GitHub and Slack. A sync upserts items and keeps your overrides, estimates and
snoozes. An item a source no longer returns is closed, but only if that source synced successfully; a failing
source changes nothing and its error shows in the brief and on the page. An item marked done comes back when the
source shows newer activity (manual items never reopen).

The page shows: Now (top 5), Reply and review (oldest first), Do (2x2 quadrants), Waiting, Today (meetings and
blocks with accept and reject), the pomodoro bar, one chip per source, an add box and Sync now.

## How scoring works

Everything is deterministic and every score has a short reason shown on the page and in `workdesk show`.

**Urgency (0-100) is the highest of:**
- due date (in the configured timezone): overdue 100, today 90, tomorrow 80, within 3 days 65, within 7 days 45, later 20
- a hot label (`incident, blocker, outage, urgent, sev1, sev2, prod-down`, configurable): 95
- respond or review, by hours since last activity: 72+ 90, 48+ 80, 24+ 70, 8+ 55, 2+ 40, else 30
- do without a due date: in progress in the active sprint 45, in the active sprint 35, else 15
- waiting: 10, or 50 ("nudge") after 5 days without activity

**Importance (0-100):** Jira priority (Highest 90, High 75, Medium 55, Low 35, Lowest 20, unset 50) plus 10 if in the
active sprint, waiting items capped at 45; review 60; respond 55 on GitHub, 50 on Slack, 60 for a Slack direct
message; waiting 35; manual 50; anything else 50. Then plus 25 for a hot label and plus 20 for a requester in
`vip_requesters`, clamped to 0..100.

Your overrides (Auto, High 85, Medium 55, Low 25, or any 0..100 from the CLI) always win. **Rank = 0.6 urgency +
0.4 importance.** Quadrants use a threshold of 60: do_now (both), schedule (important only), quick (urgent only),
later.

## Planner

Working hours 09:30-18:00 on weekdays, lunch 12:30-13:30, 5 minute buffer around meetings, blocks of 25 to 90
minutes, at most 4 focus hours a day (all configurable under `[planner]`). Candidates are open "do" items in
do_now and schedule; remaining effort is the estimate minus pomodoro minutes already logged. With 3 or more open
respond/review items a 30 minute "Replies and reviews" block goes first. Placement is greedy into the earliest
free slot. Proposals are stored and re-planning replaces only proposed ones. Nothing touches the calendar until you
accept; an accepted block becomes a private, busy event "Focus: <title>" with the item URL as description and the
private property `workdesk=1`.

## Development

```
python3 -m unittest -q
```

Tests use fixtures only (`workdesk/fixtures`). All network access goes through `workdesk/collectors/base.py`;
each response parser is one small function so a wrong field name is fixed in one place. See `DECISIONS.md` for the
choices made where the spec was silent.

Security notes: the server binds to 127.0.0.1, rejects any Host other than localhost or 127.0.0.1, rejects POSTs
whose Origin differs, limits bodies to 64 KB, serves the page with a Content-Security-Policy, and the page builds
every element with DOM APIs and `textContent` and only renders http(s) links.
