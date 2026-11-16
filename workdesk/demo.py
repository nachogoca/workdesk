"""Fixture-backed HTTP and demo mode (no tokens needed)."""
import itertools
import json
import os
import re
from datetime import datetime, timedelta, timezone

from .config import DEFAULTS, deep_merge
from .models import iso

FIXTURE_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)  # a Thursday, epoch 1768478400
FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
DEMO_ENV = {"JIRA_TOKEN": "demo", "GITHUB_TOKEN": "demo", "SLACK_TOKEN": "demo",
            "GOOGLE_CLIENT_ID": "demo-client", "GOOGLE_CLIENT_SECRET": "demo-secret"}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
TS_RE = re.compile(r"\b(\d{10})\.(\d{6})\b")


def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name)) as f:
        return json.load(f)


def _shift_date_prefix(s, days):
    d = datetime.strptime(s[:10], "%Y-%m-%d") + timedelta(days=days)
    return d.strftime("%Y-%m-%d") + s[10:]


def shift_value(v, days):
    """Recursively shift dates, ISO datetimes and Slack ts strings by whole days."""
    if not days:
        return v
    if isinstance(v, dict):
        return {k: shift_value(x, days) for k, x in v.items()}
    if isinstance(v, list):
        return [shift_value(x, days) for x in v]
    if isinstance(v, str):
        if DATE_RE.match(v) or DT_RE.match(v):
            return _shift_date_prefix(v, days)
        return TS_RE.sub(lambda m: f"{int(m.group(1)) + days * 86400}.{m.group(2)}", v)
    return v


def routes():
    """(regex, target) table; target is a fixture file name, a dict/list, an Exception or a callable."""
    counter = itertools.count(1)
    return [
        (r"^POST https://oauth2\.googleapis\.com/token", "gcal_token.json"),
        (r"^POST .*/calendars/[^/]+/events$", lambda call: {"id": f"demo-event-{next(counter)}"}),
        (r"^GET .*/calendars/[^/]+/events\?", "gcal_events.json"),
        (r"/rest/api/3/myself", "jira_myself.json"),
        (r"/rest/api/3/search/jql.*nextPageToken=tok2", "jira_search_2.json"),
        (r"/rest/api/3/search/jql", "jira_search_1.json"),
        (r"api\.github\.com/user$", "github_user.json"),
        (r"search/issues\?.*q=is:open is:pr review-requested:@me", "github_review.json"),
        (r"search/issues\?.*q=is:open is:pr author:@me", "github_authored.json"),
        (r"search/issues\?.*q=is:open assignee:@me", "github_assigned.json"),
        (r"/notifications", "github_notifications.json"),
        (r"slack\.com/api/auth\.test", "slack_auth.json"),
        (r"search\.messages\?.*query=<@U_ME>", "slack_search_mention.json"),
        (r"search\.messages\?.*query=to:me", "slack_search_tome.json"),
        (r"conversations\.replies\?channel=C_ENG&", "slack_replies_eng.json"),
        (r"conversations\.replies\?channel=C_OPS&", "slack_replies_ops.json"),
        (r"conversations\.replies\?channel=C_PRIV&", "slack_replies_priv.json"),
        (r"conversations\.history\?channel=D_DANA&", "slack_history_dana.json"),
        (r"conversations\.history\?channel=D_BOB&", "slack_history_bob.json"),
    ]


class FixtureHttp:
    """Drop-in for Http. Matches 'METHOD url?k=v&...' (params sorted, unencoded) against a route table."""

    def __init__(self, route_table=None, shift_days=0):
        self.routes = [(re.compile(r), t) for r, t in (route_table if route_table is not None else routes())]
        self.shift = shift_days
        self.calls = []

    def add_route(self, regex, target, first=True):
        entry = (re.compile(regex), target)
        self.routes.insert(0, entry) if first else self.routes.append(entry)

    def key(self, method, url, params):
        k = f"{method} {url}"
        if params:
            k += "?" + "&".join(f"{n}={params[n]}" for n in sorted(params))
        if self.shift:
            k = TS_RE.sub(lambda m: f"{int(m.group(1)) - self.shift * 86400}.{m.group(2)}", k)
        return k

    def request(self, method, url, headers=None, params=None, json_body=None, form=None):
        key = self.key(method, url, params)
        self.calls.append({"method": method, "url": url, "params": dict(params or {}), "json": json_body,
                           "form": form, "headers": dict(headers or {}), "key": key})
        for rx, target in self.routes:
            if rx.search(key):
                if isinstance(target, Exception):
                    raise target
                if callable(target):
                    return target(self.calls[-1])
                data = load_fixture(target) if isinstance(target, str) else target
                return shift_value(data, self.shift)
        raise AssertionError("no fixture route for " + key)


def demo_config():
    over = {"jira": {"enabled": True}, "github": {"enabled": True}, "slack": {"enabled": True},
            "calendar": {"enabled": True}}
    return deep_merge(DEFAULTS, over)


def demo_db_path(env):
    return os.path.expanduser(env.get("WORKDESK_DEMO_DB") or "~/.local/share/workdesk/demo.db")


def setup(env, http, now):
    """Config, env, http and db path for --demo. A given http (tests) is kept as is."""
    shift = (now.date() - FIXTURE_NOW.date()).days
    env = dict(env)
    env.update(DEMO_ENV)
    return demo_config(), env, http or FixtureHttp(shift_days=shift), demo_db_path(env)
