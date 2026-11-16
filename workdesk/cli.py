"""Command line interface. main() returns an exit code so tests can drive it."""
import argparse
import os
import sys
from datetime import timedelta

from . import config as cfgmod
from .db import Database, DbError
from .models import utcnow, iso, local_date, parse_level, KINDS
from .scoring import rank_items


class Ctx:
    def __init__(self, cfg, db, env, http, now, demo=False):
        self.cfg, self.db, self.env, self.http, self.now, self.demo = cfg, db, env, http, now, demo
        self._cal = None

    @property
    def day(self):
        return local_date(self.now, self.cfg["general"]["timezone"]).isoformat()

    def get_http(self):
        if self.http is None:
            from .collectors.base import Http
            self.http = Http()
        return self.http

    def ref(self, text):
        return self.db.resolve(text, self.day)


def build_ctx(args, env, http, now):
    env = dict(os.environ) if env is None else env
    now = now or utcnow()
    if getattr(args, "demo", False):
        from . import demo
        cfg, env, http, path = demo.setup(env, http, now)
        return Ctx(cfg, Database(path), env, http, now, demo=True)
    cfg = cfgmod.load_config(env)
    return Ctx(cfg, Database(cfgmod.db_path(cfg, env)), env, http, now)


def short(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def ranked_open(ctx):
    return rank_items(ctx.db.list_items(ctx.now), ctx.now, ctx.cfg)


def fmt_row(ctx, s):
    alias = ctx.db.alias_for(s["id"], ctx.day)
    due = s["due"] or "-"
    return f"{alias:<4} {s['rank']:>5.1f} U{s['urgency']:<3} I{s['importance']:<3} {s['kind']:<7} {due:<10} {short(s['title'], 60)}"


# commands ---------------------------------------------------------------
def cmd_init(ctx, a):
    path = cfgmod.config_path(ctx.env)
    if cfgmod.write_example(path):
        print("wrote example config to " + path)
    else:
        print("config already exists: " + path)
    return 0


def cmd_list(ctx, a):
    if a.state != "open":
        rows = ctx.db.list_items(ctx.now, (a.state,))
        for r in rows:
            print(f"{r['id']}  {r['kind']:<7} {short(r['title'], 60)}")
        return 0
    ranked = ranked_open(ctx)
    if a.kind:
        ranked = [s for s in ranked if s["kind"] == a.kind]
    for s in ranked:
        print(fmt_row(ctx, s))
    if not ranked:
        print("no open items")
    return 0


def cmd_add(ctx, a):
    if a.due:
        from datetime import date
        date.fromisoformat(a.due)
    imp = parse_level(a.importance)
    item_id = ctx.db.add_manual(a.title, ctx.now, due=a.due, kind=a.kind, url=a.url or "", importance=imp,
                                estimate=a.estimate)
    print(f"added {ctx.db.alias_for(item_id, ctx.day)} ({item_id})")
    return 0


def cmd_done(ctx, a):
    item_id = ctx.ref(a.ref)
    ctx.db.set_state(item_id, "done", ctx.now)
    print("done " + item_id)
    return 0


def parse_duration(text):
    text = text.strip().lower()
    unit = text[-1]
    if unit in "hdw" and text[:-1].isdigit():
        n = int(text[:-1])
        return timedelta(**{{"h": "hours", "d": "days", "w": "weeks"}[unit]: n})
    if text.isdigit():
        return timedelta(days=int(text))
    raise ValueError("duration like 1d, 1w, 3h")


def cmd_snooze(ctx, a):
    item_id = ctx.ref(a.ref)
    until = iso(ctx.now + parse_duration(a.duration))
    ctx.db.set_state(item_id, "snoozed", ctx.now, snooze_until=until)
    print(f"snoozed {item_id} until {until}")
    return 0


def cmd_set(ctx, a):
    item_id = ctx.ref(a.ref)
    if a.field == "estimate":
        ctx.db.set_estimate(item_id, int(a.value))
    else:
        ctx.db.set_override(item_id, a.field, parse_level(a.value))
    print(f"set {a.field} of {item_id} to {a.value}")
    return 0


def cmd_sync(ctx, a):
    from .sync import run_sync
    res = run_sync(ctx.db, ctx.cfg, ctx.get_http(), ctx.env, ctx.now)
    if not res:
        print("no sources enabled (see config: enabled = true)")
    for name, r in res.items():
        print(f"{name}: {r['count']} items" if r["ok"] else f"{name}: FAILED {r['error']}")
    return 0 if all(r["ok"] for r in res.values()) else 1


HANDLERS = {"sync": cmd_sync, "init": cmd_init, "list": cmd_list, "add": cmd_add, "done": cmd_done, "snooze": cmd_snooze,
            "set": cmd_set}


def build_parser():
    p = argparse.ArgumentParser(prog="workdesk", description="Personal work dashboard")
    p.add_argument("--demo", action="store_true", help="use fixtures and the demo database")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="write the example config")
    sub.add_parser("sync", help="pull from all enabled sources")
    s = sub.add_parser("list", help="list items by rank")
    s.add_argument("--state", default="open", choices=["open", "done", "snoozed", "closed"])
    s.add_argument("--kind", choices=KINDS)
    s = sub.add_parser("add", help="add a manual task")
    s.add_argument("title")
    s.add_argument("--due")
    s.add_argument("--kind", default="do", choices=KINDS)
    s.add_argument("--url")
    s.add_argument("--importance", help="auto|high|medium|low|0-100")
    s.add_argument("--estimate", type=int, default=50)
    s = sub.add_parser("done", help="mark an item done")
    s.add_argument("ref")
    s = sub.add_parser("snooze", help="hide an item for a while")
    s.add_argument("ref")
    s.add_argument("duration", nargs="?", default="1d")
    s = sub.add_parser("set", help="set importance, urgency or estimate")
    s.add_argument("ref")
    s.add_argument("field", choices=["importance", "urgency", "estimate"])
    s.add_argument("value")
    return p


def main(argv=None, env=None, http=None, now=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    ctx = None
    try:
        ctx = build_ctx(args, env, http, now)
        return HANDLERS[args.cmd](ctx, args) or 0
    except (DbError, ValueError) as e:
        print("error: " + str(e), file=sys.stderr)
        return 1
    finally:
        if ctx is not None:
            ctx.db.close()


if __name__ == "__main__":
    sys.exit(main())
