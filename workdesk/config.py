"""Configuration: one EXAMPLE TOML string is the single source of defaults."""
import copy
import os
import tomllib

EXAMPLE = '''# workdesk configuration. Tokens are read from environment variables only;
# this file stores the variable NAMES, never the values.

[general]
timezone = "Europe/Berlin"
port = 8765
db_path = ""                     # empty = ~/.local/share/workdesk/workdesk.db (or WORKDESK_DB)
hot_labels = ["incident", "blocker", "outage", "urgent", "sev1", "sev2", "prod-down"]
vip_requesters = []              # names (case-insensitive) that raise importance by 20
sync_max_age_min = 15            # "workdesk today" syncs first when the last sync is older

[jira]
enabled = false
base_url = "https://acme.atlassian.net"
auth = "basic"                   # basic (email + API token) or bearer
email = "me@acme.example"
token_env = "JIRA_TOKEN"
search_path = "/rest/api/3/search/jql"
jql = "assignee = currentUser() AND statusCategory != Done"
waiting_statuses = ["Blocked", "Waiting", "On Hold"]
sprint_field = "customfield_10020"
page_size = 50

[github]
enabled = false
api_url = "https://api.github.com"   # GitHub Enterprise: https://ghe.example/api/v3
token_env = "GITHUB_TOKEN"

[slack]
enabled = false
api_url = "https://slack.com/api"
token_env = "SLACK_TOKEN"
queries = ["<@{user_id}>", "to:me"]
days = 7
max_matches = 40
reaction_counts_as_done = true

[calendar]
enabled = false
client_id_env = "GOOGLE_CLIENT_ID"
client_secret_env = "GOOGLE_CLIENT_SECRET"
token_file = "~/.config/workdesk/google_token.json"
calendar_id = "primary"

[pomodoro]
focus_min = 25
break_min = 5
long_break_min = 15
long_break_after = 4

[planner]
working_start = "09:30"
working_end = "18:00"
lunch_start = "12:30"
lunch_end = "13:30"
workdays = [1, 2, 3, 4, 5]       # Monday = 1
min_block_min = 25
max_block_min = 90
buffer_min = 5
max_focus_hours = 4
replies_threshold = 3            # this many open respond/review items add a replies block
replies_block_min = 30
'''

DEFAULTS = tomllib.loads(EXAMPLE)


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config_path(env=None):
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("WORKDESK_CONFIG") or "~/.config/workdesk/config.toml")


def load_config(env=None, path=None):
    path = path or config_path(env)
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path, "rb") as f:
            cfg = deep_merge(cfg, tomllib.load(f))
    return cfg


def db_path(cfg, env=None):
    env = os.environ if env is None else env
    p = env.get("WORKDESK_DB") or cfg["general"].get("db_path") or "~/.local/share/workdesk/workdesk.db"
    return os.path.expanduser(p)


def write_example(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return False
    with open(path, "w") as f:
        f.write(EXAMPLE)
    return True
