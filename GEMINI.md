# workdesk: agent rules

This repository is "workdesk", a personal work dashboard. When the owner asks about priorities,
today, or what to work on:

1. Run `workdesk today` once (if the command is not installed, use `python3 -m workdesk today`).
2. Answer from its output only, in at most 8 lines.
3. Do NOT read the source code or the database, and do NOT call Jira, Slack, GitHub or calendar
   tools for this question. The brief already contains everything needed.
4. Use `workdesk show <alias>` only when the owner asks about one specific item
   (aliases such as j1, g2, s1, m1 are listed in the brief).
5. For changes, use the CLI, never the database:
   - `workdesk done <alias>`
   - `workdesk snooze <alias> 1d` (or `1w`)
   - `workdesk set <alias> importance|urgency|estimate <value>`
   - `workdesk plan --accept all` or `workdesk plan --accept <block ids>` (ids are in the BLOCKS lines)
   - `workdesk pomo start <alias>` / `workdesk pomo stop`
6. Add `--no-sync` to `workdesk today` only if the owner says data is fresh enough.

## Developing this repository (only when asked to change the code)

- Python 3.11+, standard library only. Tests: `python3 -m unittest -q`.
- All network access goes through `workdesk/collectors/base.py` (`Http`); tests use `FixtureHttp` from
  `workdesk/demo.py` with the JSON files in `workdesk/fixtures/`.
- Each API response parser is one small function (`parse_issue`, `parse_search_item`, `parse_notification`,
  `parse_match`, `parse_event`): fix wrong field names there.
- Record non-obvious choices in `DECISIONS.md`. Never put tokens in code, config or logs.
