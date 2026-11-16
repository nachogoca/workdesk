---
description: Today's priorities from workdesk, answered cheaply
---

Run `workdesk today` exactly once (fall back to `python3 -m workdesk today` if the command is not installed)
and answer from its output only, in at most 8 lines.

Do not read source code or the database, and do not call Jira, Slack, GitHub or calendar tools.
Use `workdesk show <alias>` only if I ask about one item. For changes use the CLI:
`workdesk done|snooze|set <alias> ...`, `workdesk plan --accept all`, `workdesk pomo start <alias>`.
