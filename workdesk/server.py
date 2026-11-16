"""Local web server: static page plus JSON API, bound to 127.0.0.1."""
import json
import os
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs

from . import pomodoro
from .db import DbError
from .models import KINDS, parse_level, utcnow, iso
from .scoring import rank_items

MAX_BODY = 65536
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def need(body, key, typ, optional=False):
    v = body.get(key)
    if v is None:
        if optional:
            return None
        raise ApiError(400, f"missing field: {key}")
    if typ is int and (isinstance(v, bool) or not isinstance(v, int)):
        raise ApiError(400, f"field {key} must be an integer")
    if typ is str and not isinstance(v, str):
        raise ApiError(400, f"field {key} must be a string")
    return v


def valid_day(text, default):
    if not text:
        return default
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ApiError(400, "day must be YYYY-MM-DD")


# endpoints: fn(ctx, body, query) -> dict ----------------------------------------
def items_payload(ctx):
    ranked = rank_items(ctx.db.list_items(ctx.now), ctx.now, ctx.cfg)
    totals = pomodoro.item_totals(ctx.db)
    for s in ranked:
        s["alias"] = ctx.db.alias_for(s["id"], ctx.day)
        s["pomo"] = totals.get(s["id"], {"count": 0, "minutes": 0})
    syncs = ctx.db.last_syncs()
    sources = {n: dict(syncs.get(n, {}), enabled=bool(ctx.cfg[n]["enabled"])) for n in ("jira", "github", "slack")}
    return {"now": iso(ctx.now), "tz": ctx.cfg["general"]["timezone"], "day": ctx.day, "items": ranked,
            "sources": sources, "pomodoro": dict(ctx.cfg["pomodoro"]),
            "timer": pomodoro.status(ctx.db, ctx.cfg, ctx.now)}


def api_items(ctx, body, q):
    return items_payload(ctx)


def api_add(ctx, body, q):
    title = need(body, "title", str).strip()
    if not title or len(title) > 300:
        raise ApiError(400, "title must be 1..300 characters")
    due = body.get("due") or None
    if due:
        valid_day(due, None)
    kind = body.get("kind") or "do"
    if kind not in KINDS:
        raise ApiError(400, "invalid kind")
    url = body.get("url") or ""
    if url and not url.lower().startswith(("http://", "https://")):
        raise ApiError(400, "url must start with http:// or https://")
    imp = body.get("importance")
    imp = parse_level(imp) if imp is not None else None
    est = body.get("estimate", 50)
    item_id = ctx.db.add_manual(title, ctx.now, due=due, kind=kind, url=url, importance=imp, estimate=50)
    if est != 50:
        ctx.db.set_estimate(item_id, est)
    return {"id": item_id, "alias": ctx.db.alias_for(item_id, ctx.day)}


def api_state(ctx, body, q):
    item_id, state = need(body, "id", str), need(body, "state", str)
    if state == "snoozed":
        days = need(body, "days", int)
        if not 1 <= days <= 365:
            raise ApiError(400, "days must be 1..365")
        ctx.db.set_state(item_id, "snoozed", ctx.now, snooze_until=iso(ctx.now + timedelta(days=days)))
    elif state in ("done", "open"):
        ctx.db.set_state(item_id, state, ctx.now)
    else:
        raise ApiError(400, "state must be done, open or snoozed")
    return {"ok": True}


def api_override(ctx, body, q):
    value = body.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100):
        raise ApiError(400, "value must be null or 0..100")
    ctx.db.set_override(need(body, "id", str), need(body, "field", str), value)
    return {"ok": True}


def api_estimate(ctx, body, q):
    ctx.db.set_estimate(need(body, "id", str), need(body, "minutes", int))
    return {"ok": True}


def api_sync(ctx, body, q):
    from .sync import run_sync
    return {"results": run_sync(ctx.db, ctx.cfg, ctx.get_http(), ctx.env, ctx.now)}


def api_pomo_status(ctx, body, q):
    return pomodoro.status(ctx.db, ctx.cfg, ctx.now)


def api_pomo_start(ctx, body, q):
    pomodoro.start(ctx.db, ctx.cfg, ctx.now, item_id=body.get("item_id"), kind=body.get("kind") or "focus")
    return pomodoro.status(ctx.db, ctx.cfg, ctx.now)


def api_pomo_stop(ctx, body, q):
    pomodoro.stop(ctx.db, ctx.now)
    return pomodoro.status(ctx.db, ctx.cfg, ctx.now)


def api_plan_get(ctx, body, q):
    from . import planner
    day = valid_day((q.get("day") or [None])[0], ctx.day)
    return planner.day_view(ctx.db, ctx.cfg, day, ctx.calendar, ctx.now)


def api_plan_post(ctx, body, q):
    from . import planner
    day = valid_day(body.get("day"), ctx.day)
    planner.plan(ctx.db, ctx.cfg, day, ctx.calendar, ctx.now)
    return planner.day_view(ctx.db, ctx.cfg, day, ctx.calendar, ctx.now)


def api_block_accept(ctx, body, q):
    from . import planner
    return {"block": planner.accept(ctx.db, ctx.cfg, need(body, "id", int), ctx.calendar, ctx.now)}


def api_block_reject(ctx, body, q):
    from . import planner
    return {"block": planner.reject(ctx.db, need(body, "id", int))}


ROUTES = {
    ("GET", "/api/items"): api_items, ("POST", "/api/add"): api_add, ("POST", "/api/state"): api_state,
    ("POST", "/api/override"): api_override, ("POST", "/api/estimate"): api_estimate,
    ("POST", "/api/sync"): api_sync, ("GET", "/api/pomodoro/status"): api_pomo_status,
    ("POST", "/api/pomodoro/start"): api_pomo_start, ("POST", "/api/pomodoro/stop"): api_pomo_stop,
    ("GET", "/api/plan"): api_plan_get, ("POST", "/api/plan"): api_plan_post,
    ("POST", "/api/block/accept"): api_block_accept, ("POST", "/api/block/reject"): api_block_reject,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "workdesk"

    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if ":" in host else host
        return name in ("localhost", "127.0.0.1")

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def dispatch(self, method):
        try:
            if not self.host_ok():
                return self.reply(403, {"error": "forbidden host"})
            if method == "POST" and not self.origin_ok():
                return self.reply(403, {"error": "forbidden origin"})
            parts = urlsplit(self.path)
            if method == "GET" and parts.path in ("/", "/index.html"):
                with open(STATIC, "rb") as f:
                    return self.reply(200, f.read(), "text/html; charset=utf-8")
            fn = ROUTES.get((method, parts.path))
            if fn is None:
                return self.reply(404, {"error": "not found"})
            body = {}
            if method == "POST":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self.reply(400, {"error": "bad Content-Length"})
                if n > MAX_BODY:
                    return self.reply(413, {"error": "request body too large"})
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw.strip() else {}
                except ValueError:
                    return self.reply(400, {"error": "body must be JSON"})
                if not isinstance(body, dict):
                    return self.reply(400, {"error": "body must be a JSON object"})
            ctx = self.server.ctx
            ctx.now = self.server.clock()
            return self.reply(200, fn(ctx, body, parse_qs(parts.query)))
        except ApiError as e:
            self.reply(e.status, {"error": str(e)})
        except (DbError, ValueError) as e:
            self.reply(400, {"error": str(e)})
        except Exception as e:  # never leak details or tokens
            self.reply(500, {"error": "internal error: " + type(e).__name__})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


class WorkdeskServer(HTTPServer):
    def __init__(self, addr, ctx, clock):
        super().__init__(addr, Handler)
        self.ctx, self.clock = ctx, clock


def make_server(ctx, port=8765, clock=None):
    return WorkdeskServer(("127.0.0.1", port), ctx, clock or utcnow)
