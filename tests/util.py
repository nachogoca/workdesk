import copy
from datetime import datetime, timedelta, timezone

from workdesk.config import DEFAULTS
from workdesk.db import Database
from workdesk.models import Item, iso

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)  # Thursday


def cfg(**over):
    c = copy.deepcopy(DEFAULTS)
    for k, v in over.items():
        sec, key = k.split("__")
        c[sec][key] = v
    return c


def mem_db():
    return Database(":memory:")


def ago(hours=0, days=0, now=NOW):
    return iso(now - timedelta(hours=hours, days=days))


def item(id="jira:MLP-1", source="jira", kind="do", title="t", **kw):
    kw.setdefault("updated_at", ago(1))
    kw.setdefault("created_at", ago(days=3))
    return Item(id=id, source=source, kind=kind, title=title, **kw)


def sitem(**kw):
    """A dict shaped like a DB item, for scoring tests."""
    d = dict(id="jira:MLP-1", source="jira", kind="do", title="t", url="", requester="", status="", due=None,
             created_at=ago(days=3), updated_at=ago(1), labels=[], meta={}, importance_override=None,
             urgency_override=None, estimate_min=50)
    d.update(kw)
    return d
