"""Run collectors independently; a failure changes nothing and is logged."""
from .collectors.github import GitHubCollector
from .collectors.jira import JiraCollector
from .collectors.slack import SlackCollector
from .models import iso, parse_iso

COLLECTORS = {"jira": JiraCollector, "github": GitHubCollector, "slack": SlackCollector}


def build_collectors(cfg, http, env):
    return [cls(cfg[name], http, env) for name, cls in COLLECTORS.items() if cfg[name].get("enabled")]


def run_sync(db, cfg, http, env, now, collectors=None):
    """Returns {source: {"ok": bool, "count": int, "error": str|None}}."""
    result = {}
    for c in collectors if collectors is not None else build_collectors(cfg, http, env):
        try:
            items = c.collect(now)
            db.upsert_items(items, now)
            db.close_missing(c.name, [i.id for i in items], now)
            db.log_sync(c.name, True, None, len(items), now, now)
            result[c.name] = {"ok": True, "count": len(items), "error": None}
        except Exception as e:  # one failing source must never block the others
            msg = f"{type(e).__name__}: {e}"
            db.log_sync(c.name, False, msg, 0, now, now)
            result[c.name] = {"ok": False, "count": 0, "error": msg}
    return result


def is_stale(db, now, max_age_min):
    last = parse_iso(db.last_sync_at())
    return last is None or (now - last).total_seconds() > max_age_min * 60


def sync_if_stale(db, cfg, http, env, now):
    if is_stale(db, now, cfg["general"]["sync_max_age_min"]):
        return run_sync(db, cfg, http, env, now)
    return None
