"""Daily brief: a few lines of plain text (or compact JSON) answering "what are today's priorities?"."""
import json
from zoneinfo import ZoneInfo

from . import planner, pomodoro
from .models import local_date, parse_iso
from .scoring import rank_items, hours_since

MAX_LINES = 30
MAX_CHARS = 1800
TITLE_STEPS = (60, 50, 40, 30, 24, 18, 12)
CAPS = {"top": 5, "reply": 3, "nudge": 3, "blocks": 6, "failed": 4}


def cut(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: max(1, n - 1)] + "~"


def _age(item, now):
    h = hours_since(item, now)
    return f"{int(h)}h" if h < 48 else f"{int(h // 24)}d"


def build_data(db, cfg, now, calendar, tmax=60):
    pomodoro.finalize(db, now)
    tz = cfg["general"]["timezone"]
    today = local_date(now, tz)
    day = today.isoformat()
    ranked = rank_items(db.list_items(now), now, cfg)
    alias = lambda i: db.alias_for(i["id"], day)  # noqa: E731
    non_wait = [i for i in ranked if i["kind"] != "waiting"]
    replies = sorted((i for i in ranked if i["kind"] in ("respond", "review")), key=lambda i: i["updated_at"] or "")
    nudges = [i for i in ranked if i["nudge"]]
    overdue = sum(1 for i in ranked if i["due"] and i["due"] < day)
    data = {
        "date": today.strftime("%a ") + day,
        "counts": {"open": len(ranked), "do_now": sum(1 for i in ranked if i["quadrant"] == "do_now"),
                   "reply": len(replies), "waiting": sum(1 for i in ranked if i["kind"] == "waiting"),
                   "nudge": len(nudges), "overdue": overdue},
        "top": [{"alias": alias(i), "title": cut(i["title"], tmax),
                 "reason": cut(i["urgency_reason"] if i["urgency"] >= 60 else i["importance_reason"], 40)}
                for i in non_wait[: CAPS["top"]]],
        "reply": {"count": len(replies),
                  "oldest": [{"alias": alias(i), "title": cut(i["title"], tmax), "age": _age(i, now)}
                             for i in replies[: CAPS["reply"]]]},
        "nudge": [{"alias": alias(i), "title": cut(i["title"], tmax), "quiet": _age(i, now)}
                  for i in nudges[: CAPS["nudge"]]],
    }
    view = planner.day_view(db, cfg, day, calendar, now)
    data["calendar"] = {"meetings": len(view["meetings"]), "meeting_h": round(view["meeting_min"] / 60, 1),
                        "free_focus_h": round(view["free_focus_min"] / 60, 1),
                        "enabled": view["calendar_enabled"], "error": cut(view["calendar_error"] or "", 80) or None}
    z = ZoneInfo(tz)
    fmt = lambda ts: parse_iso(ts).astimezone(z).strftime("%H:%M")  # noqa: E731
    data["blocks"] = [{"id": b["id"], "start": fmt(b["start"]), "end": fmt(b["end"]),
                       "title": cut(b["title"], tmax), "status": b["status"]}
                      for b in view["blocks"] if b["status"] != "rejected"][: CAPS["blocks"]]
    yesterday = (today.fromordinal(today.toordinal() - 1)).isoformat()
    data["yesterday"] = pomodoro.completed_on(db, yesterday, cfg)
    data["failed"] = [{"source": s, "error": cut(r["error"] or "", max(30, tmax + 20))}
                      for s, r in db.last_syncs().items()
                      if r.get("ok") is False and cfg.get(s, {}).get("enabled")][: CAPS["failed"]]
    return data


def render_text(d):
    c = d["counts"]
    lines = [f"{d['date']}: {c['open']} open, {c['do_now']} do-now, {c['reply']} to answer, "
             f"{c['waiting']} waiting ({c['nudge']} to nudge), {c['overdue']} overdue", "TOP"]
    lines += [f"{t['alias']} {t['title']} | {t['reason']}" for t in d["top"]] or ["none"]
    if d["reply"]["count"]:
        lines.append(f"REPLY {d['reply']['count']}")
        lines += [f"{r['alias']} {r['title']} | {r['age']}" for r in d["reply"]["oldest"]]
    if d["nudge"]:
        lines.append("NUDGE")
        lines += [f"{n['alias']} {n['title']} | quiet {n['quiet']}" for n in d["nudge"]]
    cal = d["calendar"]
    lines.append(f"CALENDAR {cal['meetings']} meetings {cal['meeting_h']}h, {cal['free_focus_h']}h free focus"
                 + ("" if cal["enabled"] else " (calendar off)") + (f" [{cal['error']}]" if cal["error"] else ""))
    if d["blocks"]:
        lines.append("BLOCKS")
        lines += [f"block {b['id']} {b['start']}-{b['end']} {b['title']} ({b['status']})" for b in d["blocks"]]
    lines.append(f"YESTERDAY {d['yesterday']} pomodoros")
    for f in d["failed"]:
        lines.append(f"SYNC FAILED {f['source']}: {f['error']}")
    return "\n".join(lines)


def within_limits(text):
    return text.count("\n") + 1 <= MAX_LINES and len(text) <= MAX_CHARS


def make(db, cfg, now, calendar):
    """(data, text) that respect the 30 line / 1800 character limits (lines are shortened until they fit)."""
    for tmax in TITLE_STEPS:
        data = build_data(db, cfg, now, calendar, tmax)
        text = render_text(data)
        if within_limits(text):
            return data, text
    lines = text.split("\n")[:MAX_LINES]  # last resort, practically unreachable
    return data, "\n".join(lines)[:MAX_CHARS]


def render_json(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def show_text(db, cfg, now, item_id):
    """Full detail of one item (only printed on request)."""
    from .scoring import score_item
    raw = db.get_item(item_id)
    if raw is None:
        raise ValueError("no such item: " + item_id)
    s = score_item(raw, now, cfg)
    t = pomodoro.item_totals(db).get(item_id, {"count": 0, "minutes": 0})
    rows = [("id", s["id"]), ("title", s["title"]), ("source/kind", f"{s['source']}/{s['kind']}"),
            ("state", s["state"] + (f" until {s['snooze_until']}" if s["snooze_until"] else "")),
            ("url", s["url"]), ("requester", s["requester"]), ("status", s["status"]), ("due", s["due"]),
            ("created", s["created_at"]), ("updated", s["updated_at"]), ("labels", ", ".join(s["labels"])),
            ("meta", json.dumps(s["meta"], sort_keys=True)),
            ("urgency", f"{s['urgency']} ({s['urgency_reason']})"),
            ("importance", f"{s['importance']} ({s['importance_reason']})"),
            ("rank/quadrant", f"{s['rank']} {s['quadrant']}"),
            ("estimate", f"{s['estimate_min']} min; logged {t['minutes']} min in {t['count']} pomodoros")]
    return "\n".join(f"{k}: {v}" for k, v in rows if v not in (None, ""))
