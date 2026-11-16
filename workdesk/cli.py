"""Command line interface. main() returns an exit code so tests can drive it."""
import argparse
import os
import sys
from datetime import timedelta

from . import config as cfgmod
from .collectors.base import ConfigError
from .db import Database, DbError
from .planner import PlannerError
from .models import utcnow, iso, local_date, parse_level, KINDS
from .scoring import rank_items


class Ctx:
    def __init__(self, cfg, db, env, http, now, demo=False):
        self.cfg, self.db, self.env, self.http, self.now, self.demo = cfg, db, env, http, now, demo
        self._cal = None

    @property
    def day(self):
        return local_date(self.now, self.cfg["general"]["timezone"]).isoformat()

    @property
    def calendar(self):
        """GoogleCalendar when enabled, else None (planner then uses working hours only)."""
        if self._cal is None and self.cfg["calendar"]["enabled"]:
            from .collectors.gcal import GoogleCalendar, MemoryTokenStore
            store = MemoryTokenStore({"refresh_token": "demo-refresh"}) if self.demo else None
            self._cal = GoogleCalendar(self.cfg["calendar"], self.get_http(), self.env, store)
        return self._cal

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
        if args.cmd == "demo" and os.path.exists(path):
            os.remove(path)
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


def serve(ctx, port, clock=None):
    from .server import make_server
    srv = make_server(ctx, port, clock)
    print(f"workdesk serving on http://127.0.0.1:{srv.server_address[1]}  (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_serve(ctx, a):
    return serve(ctx, a.port or ctx.cfg["general"]["port"])


def cmd_demo(ctx, a):
    from datetime import timedelta
    from .sync import run_sync
    res = run_sync(ctx.db, ctx.cfg, ctx.get_http(), ctx.env, ctx.now)
    due = (local_date(ctx.now, ctx.cfg["general"]["timezone"]) + timedelta(days=5)).isoformat()
    ctx.db.add_manual("Prepare notes for the sprint demo", ctx.now, due=ctx.day, importance=85, estimate=60)
    ctx.db.add_manual("Book dentist appointment", ctx.now, due=due, estimate=25)
    from . import planner
    planner.plan(ctx.db, ctx.cfg, ctx.day, ctx.calendar, ctx.now)
    print("demo database rebuilt: " + ", ".join(f"{n} {r['count']}" for n, r in res.items()) + ", 2 manual tasks")
    if a.no_serve:
        return 0
    return serve(ctx, a.port or ctx.cfg["general"]["port"])


def pomo_line(st):
    t = st["today"]
    tail = f"today: {t['completed']} pomodoros, {t['focus_min']} min focus"
    if not st["running"]:
        return "idle; " + tail
    m, sec = divmod(st["remaining_s"], 60)
    what = f" on {short(st['item_title'], 50)}" if st.get("item_title") else ""
    return f"{st['kind']} {m:02d}:{sec:02d} left{what}; " + tail


def cmd_pomo(ctx, a):
    from . import pomodoro
    if a.action == "start":
        item_id = ctx.ref(a.ref) if a.ref else None
        pomodoro.start(ctx.db, ctx.cfg, ctx.now, item_id=item_id, kind="break" if a.brk else "focus")
    elif a.action == "stop":
        if pomodoro.stop(ctx.db, ctx.now) is None:
            print("no timer running")
    print(pomo_line(pomodoro.status(ctx.db, ctx.cfg, ctx.now)))
    return 0


def hhmm(ts, tz):
    from zoneinfo import ZoneInfo
    from .models import parse_iso
    return parse_iso(ts).astimezone(ZoneInfo(tz)).strftime("%H:%M")


def block_line(b, tz):
    return f"  [{b['id']}] {hhmm(b['start'], tz)}-{hhmm(b['end'], tz)} {short(b['title'], 50)} ({b['status']})"


def cmd_plan(ctx, a):
    from . import planner
    day = a.day or ctx.day
    if a.day:
        from datetime import date
        date.fromisoformat(a.day)
    tz, cal = ctx.cfg["general"]["timezone"], ctx.calendar
    rc = 0
    existing = [b for b in planner.day_view(ctx.db, ctx.cfg, day, cal, ctx.now)["blocks"]
                if b["status"] == "proposed"]
    if not (a.accept and existing):
        planner.plan(ctx.db, ctx.cfg, day, cal, ctx.now)
    if a.accept:
        blocks = [b for b in planner.day_view(ctx.db, ctx.cfg, day, cal, ctx.now)["blocks"]
                  if b["status"] == "proposed"]
        if a.accept != "all":
            want = {int(x) for x in a.accept.replace(" ", "").split(",") if x}
            blocks = [b for b in blocks if b["id"] in want]
            for missing in sorted(want - {b["id"] for b in blocks}):
                print(f"error: block {missing} is not a proposed block for {day}", file=sys.stderr)
                rc = 1
        for b in blocks:
            try:
                planner.accept(ctx.db, ctx.cfg, b["id"], cal, ctx.now)
            except planner.PlannerError as e:
                print("error: " + str(e), file=sys.stderr)
                rc = 1
    view = planner.day_view(ctx.db, ctx.cfg, day, cal, ctx.now)
    print(f"{day}: {len(view['meetings'])} meetings ({view['meeting_min'] / 60:.1f}h), "
          f"{view['free_focus_min'] / 60:.1f}h free focus" + ("" if view["calendar_enabled"] else " (calendar off)"))
    if view["calendar_error"]:
        print("calendar: " + view["calendar_error"])
    for m in view["meetings"]:
        print(f"  {hhmm(m['start'], tz)}-{hhmm(m['end'], tz)} meeting: {short(m['title'], 50)}")
    for b in view["blocks"]:
        if b["status"] != "rejected":
            print(block_line(b, tz))
    if a.ics:
        text, n = planner.to_ics(ctx.db, day, ctx.now)
        with open(a.ics, "w", newline="") as f:
            f.write(text)
        print(f"wrote {n} accepted blocks to {a.ics}")
    return rc


def cmd_auth(ctx, a):
    from .collectors.base import ConfigError
    if not ctx.cfg["calendar"]["enabled"]:
        raise ConfigError("calendar is disabled: set enabled = true in [calendar]")
    ctx.calendar.authorize()
    print("google authorisation stored")
    return 0


def cmd_today(ctx, a):
    from . import brief, planner
    from .sync import sync_if_stale
    if not a.no_sync:
        sync_if_stale(ctx.db, ctx.cfg, ctx.get_http(), ctx.env, ctx.now)
    planner.plan(ctx.db, ctx.cfg, ctx.day, ctx.calendar, ctx.now)
    data, text = brief.make(ctx.db, ctx.cfg, ctx.now, ctx.calendar)
    print(brief.render_json(data) if a.json else text)
    return 0


def cmd_show(ctx, a):
    from . import brief
    print(brief.show_text(ctx.db, ctx.cfg, ctx.now, ctx.ref(a.ref)))
    return 0


def explain(name, e):
    """Short human reason for a failed check; never includes tokens or headers."""
    from .collectors.base import HttpError
    from .collectors.slack import SlackError
    if isinstance(e, ConfigError):
        return str(e)
    if isinstance(e, HttpError):
        return {0: "network error (cannot reach the server)", 401: "401 unauthorised (bad or expired token)",
                403: "403 forbidden (missing scope or permission)"}.get(e.status, f"HTTP {e.status}")
    if isinstance(e, SlackError):
        msg = str(e)
        if "missing_scope" in msg:
            return "missing scope: " + msg
        if any(x in msg for x in ("invalid_auth", "not_authed", "token_revoked", "account_inactive")):
            return "bad token: " + msg
        return msg
    return f"{type(e).__name__}: {e}"


def cmd_doctor(ctx, a):
    from .sync import build_collectors
    checks = {c.name: c for c in build_collectors(ctx.cfg, ctx.get_http(), ctx.env)}
    if ctx.cfg["calendar"]["enabled"]:
        checks["calendar"] = ctx.calendar
    failed = False
    for name in ("jira", "github", "slack", "calendar"):
        if name not in checks:
            print(f"skip {name}: disabled")
            continue
        try:
            print(f"pass {name}: {checks[name].check()}")
        except Exception as e:  # report every failure kind as one line
            failed = True
            print(f"FAIL {name}: {explain(name, e)}")
    return 1 if failed else 0


def cmd_sync(ctx, a):
    from .sync import run_sync
    res = run_sync(ctx.db, ctx.cfg, ctx.get_http(), ctx.env, ctx.now)
    if not res:
        print("no sources enabled (see config: enabled = true)")
    for name, r in res.items():
        print(f"{name}: {r['count']} items" if r["ok"] else f"{name}: FAILED {r['error']}")
    return 0 if all(r["ok"] for r in res.values()) else 1


HANDLERS = {"doctor": cmd_doctor, "today": cmd_today, "show": cmd_show, "plan": cmd_plan, "auth": cmd_auth, "pomo": cmd_pomo, "sync": cmd_sync, "serve": cmd_serve, "demo": cmd_demo, "init": cmd_init, "list": cmd_list, "add": cmd_add, "done": cmd_done, "snooze": cmd_snooze,
            "set": cmd_set}


def build_parser():
    p = argparse.ArgumentParser(prog="workdesk", description="Personal work dashboard")
    p.add_argument("--demo", action="store_true", help="use fixtures and the demo database")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="write the example config")
    sub.add_parser("sync", help="pull from all enabled sources")
    s = sub.add_parser("pomo", help="pomodoro timer: start [ref] | stop | status")
    s.add_argument("action", choices=["start", "stop", "status"])
    s.add_argument("ref", nargs="?")
    s.add_argument("--break", dest="brk", action="store_true", help="start a break instead of focus")
    s = sub.add_parser("plan", help="propose (and optionally accept) focus blocks")
    s.add_argument("--day", help="YYYY-MM-DD (default today)")
    s.add_argument("--accept", help="'all' or comma-separated block ids")
    s.add_argument("--ics", help="write accepted blocks of the day to this .ics file")
    s = sub.add_parser("auth", help="authorise an external service")
    s.add_argument("service", choices=["google"])
    s = sub.add_parser("today", help="daily brief (plain text, at most 30 lines)")
    s.add_argument("--no-sync", action="store_true", help="skip the sync even when data is stale")
    s.add_argument("--json", action="store_true", help="same content as compact JSON")
    s = sub.add_parser("show", help="full detail of one item (alias or id)")
    s.add_argument("ref")
    sub.add_parser("doctor", help="one cheap call per enabled source, pass or fail")
    s = sub.add_parser("serve", help="serve the web page")
    s.add_argument("--port", type=int)
    s = sub.add_parser("demo", help="rebuild the demo database from fixtures and serve it")
    s.add_argument("--port", type=int)
    s.add_argument("--no-serve", action="store_true")
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
    if args.cmd == "demo":
        args.demo = True
    try:
        ctx = build_ctx(args, env, http, now)
        return HANDLERS[args.cmd](ctx, args) or 0
    except (DbError, ValueError, ConfigError, PlannerError) as e:
        print("error: " + str(e), file=sys.stderr)
        return 1
    finally:
        if ctx is not None:
            ctx.db.close()


if __name__ == "__main__":
    sys.exit(main())
