# Decisions

Choices made where the spec was silent or ambiguous (the owner was away).

## Time and data
- `now` is an injected aware UTC datetime everywhere; timestamps are stored as `YYYY-MM-DDTHH:MM:SSZ` strings.
- Extra DB columns beyond the spec: `pomodoros.kind` (focus|break, so a break survives reload), `blocks.title` and
  `blocks.day` (the replies block has `item_id` NULL), and table `aliases(day, alias, item_id)`.
- Due-date day differences use the configured timezone; a "do" item with a due date uses the due rule only (plus hot label).
- Urgency takes the highest applicable rule for any kind, so a waiting item with an overdue due date scores 100.
- Waiting nudge threshold is >= 120 hours without activity.
- Manual task importance is stored as `importance_override`; manual ids are `manual:<8 hex>`.
- Snoozes expire lazily on read (`snooze_until <= now`).
- `done` items reopen only when `source != manual` and `updated_at > state_changed_at`; a closed item that reappears reopens.
- Last-sync age for "sync first if older than 15 minutes" is the newest sync_log row of any source.

## Collectors
- Collectors return `Item` dataclasses; parsers: `parse_issue`, `parse_search_item`, `parse_notification`, `parse_match`, `parse_event`.
- Jira paging stops when no `nextPageToken` is returned (max 20 pages). Requester is the reporter.
- GitHub: one page of 50 per search, no further pagination. On duplicates the richer search record is kept, kind by
  precedence review > respond > do > waiting, latest `updated_at`. A notification-only item takes its title from the subject.
- Slack: each DM message without a thread is its own item (history check); channel and threaded messages are grouped per
  thread root. Item id uses the oldest unanswered mention, `updated_at` the newest. DM titles are `@username: text`.
  Any failure of the answered check (API error or HTTP error) keeps the item. Mention markup like `<@U123>` is left as is.
- The Google Calendar is not an items source and not part of sync; its errors surface in plan/brief/doctor only.
- Calendar events are returned as UTC; the daily window is local midnight to midnight in the configured timezone.
  Declined = my own attendee entry is "declined".

## Planner
- All slot arithmetic is in UTC. Today's slots start at `now` rounded up to 5 minutes.
- Daily cap counts accepted blocks and the replies block. A remaining effort below 25 min is rounded up to 25 for the
  first chunk; a sub-25 leftover after a split is dropped.
- Items or the replies block with a rejected block that day are not proposed again that day.
- `plan --accept` accepts existing proposed blocks and plans first only when no proposal exists. If the calendar insert
  fails the block stays proposed and the error is reported. Without a calendar, accepting only changes local status.
- `workdesk today` re-plans the day (replacing proposed blocks), so block ids in the brief are those of the latest run.
- A calendar read error does not stop planning: it falls back to working hours only and reports the error.

## Brief
- Aliases are prefix (j/g/s/m) + next number for the day, kept for the whole day; rows of earlier days are deleted.
  Commands accept an alias of today or a full item id.
- Sections with nothing to show (REPLY, NUDGE, BLOCKS, failures) are omitted; TOP, CALENDAR and YESTERDAY always print.
  TOP excludes waiting items. Caps: 5 top, 3 reply, 3 nudge, 6 blocks, 4 failures; titles are shortened step by step
  (60 down to 12 characters) until the text fits 30 lines and 1800 characters.
- Failed sources come from the latest sync_log row per enabled source.

## Server and page
- The server is a plain single-threaded `HTTPServer` (sqlite connection with `check_same_thread=False`).
- `GET /api/plan` returns the day view without re-planning; `POST /api/plan` re-plans.
- CSP is `default-src 'none'` with inline script and style allowed (single-file page) and `connect-src 'self'`.
- Page timer ticks locally from `ends_at` and polls status every 15 s; when a focus timer ends the page starts the break.
  After a reload past the end, the break is not started automatically.
- Page times render in the browser's timezone; the planner uses the configured timezone.

## Demo and tests
- `--demo` is a global flag; `demo` implies it. Demo DB path: `WORKDESK_DEMO_DB` or `~/.local/share/workdesk/demo.db`.
- The demo shifts fixtures by whole days only, so fixture clock times are not adjusted to the current time of day.
- The real Google OAuth flow, real Jira/GitHub/Slack responses and the page in a real browser could not be tested
  (no tokens, no browser here); the page JavaScript was only syntax-checked with `node --check`.
- `pyproject.toml` lists the packages explicitly; `pip install -e .` was not run (pip is not allowed in this task).
