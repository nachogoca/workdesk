"""Time blocking: propose focus blocks in free time, accept them into the calendar or an .ics file."""
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from . import pomodoro
from .models import iso, parse_iso
from .scoring import rank_items

REPLIES_TITLE = "Replies and reviews"
STEP = 5


class PlannerError(Exception):
    pass


def _local(day, hhmm, tz):
    h, m = hhmm.split(":")
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=ZoneInfo(tz)).astimezone(ZoneInfo("UTC"))


def is_workday(day, cfg):
    return date.fromisoformat(day).isoweekday() in cfg["planner"]["workdays"]


def subtract(slots, cut):
    a, b = cut
    out = []
    for s, e in slots:
        if b <= s or a >= e:
            out.append((s, e))
            continue
        if a > s:
            out.append((s, a))
        if b < e:
            out.append((b, e))
    return out


def round_up(dt, step=STEP):
    base = dt.replace(second=0, microsecond=0)
    if base < dt:
        base += timedelta(minutes=1)
    return base + timedelta(minutes=(-base.minute) % step)


def free_slots(day, cfg, busy, now, accepted=()):
    """Working window minus lunch, meetings (+buffer) and accepted blocks; nothing before `now` today."""
    p, tz = cfg["planner"], cfg["general"]["timezone"]
    if not is_workday(day, cfg):
        return []
    slots = [(_local(day, p["working_start"], tz), _local(day, p["working_end"], tz))]
    slots = subtract(slots, (_local(day, p["lunch_start"], tz), _local(day, p["lunch_end"], tz)))
    buf = timedelta(minutes=p["buffer_min"])
    for s, e in busy:
        slots = subtract(slots, (s - buf, e + buf))
    for s, e in accepted:
        slots = subtract(slots, (s, e))
    if now.astimezone(ZoneInfo(tz)).date().isoformat() == day:
        slots = subtract(slots, (datetime(2000, 1, 1, tzinfo=now.tzinfo), round_up(now)))
    return [(s, e) for s, e in slots if e > s]


def _minutes(a, b):
    return int((b - a).total_seconds() // 60)


def fetch_busy(cfg, day, calendar):
    """([(start, end)], error, events). A calendar failure never breaks planning."""
    if calendar is None:
        return [], None, []
    try:
        ev = calendar.busy(day, cfg["general"]["timezone"])
        return [(parse_iso(e["start"]), parse_iso(e["end"])) for e in ev], None, ev
    except Exception as e:  # noqa
        return [], f"{type(e).__name__}: {e}", []


def _block_rows(db, day):
    rows = db.rows('SELECT b.id, b.item_id, b.title, b.start, b."end" AS "end", b.status, b.calendar_event_id,'
                   ' i.url AS url FROM blocks b LEFT JOIN items i ON i.id = b.item_id WHERE b.day=? ORDER BY b.start',
                   (day,))
    for r in rows:
        r["url"] = r["url"] or ""
    return rows


def _accepted(db, day):
    return [(parse_iso(b["start"]), parse_iso(b["end"])) for b in _block_rows(db, day) if b["status"] == "accepted"]


def day_view(db, cfg, day, calendar, now):
    busy_dt, err, events = fetch_busy(cfg, day, calendar)
    p = cfg["planner"]
    tz = cfg["general"]["timezone"]
    blocks = _block_rows(db, day)
    slots = free_slots(day, cfg, busy_dt, now, _accepted(db, day))
    accepted_min = sum(_minutes(s, e) for s, e in _accepted(db, day))
    cap = int(p["max_focus_hours"] * 60)
    ws, we = _local(day, p["working_start"], tz), _local(day, p["working_end"], tz)
    meeting_min = sum(max(0, _minutes(max(s, ws), min(e, we))) for s, e in busy_dt) if is_workday(day, cfg) else 0
    free = min(sum(_minutes(s, e) for s, e in slots), max(0, cap - accepted_min))
    return {"day": day, "tz": tz, "meetings": events, "blocks": blocks, "calendar_enabled": calendar is not None,
            "calendar_error": err, "meeting_min": meeting_min, "free_focus_min": free, "focus_cap_min": cap,
            "workday": is_workday(day, cfg)}


def plan(db, cfg, day, calendar, now):
    """Replace this day's proposed blocks with a fresh greedy plan. Returns the new proposals."""
    p = cfg["planner"]
    db.run("DELETE FROM blocks WHERE day=? AND status='proposed'", (day,))
    busy_dt, _err, _ev = fetch_busy(cfg, day, calendar)
    rows = _block_rows(db, day)
    slots = free_slots(day, cfg, busy_dt, now, _accepted(db, day))
    cap_left = int(p["max_focus_hours"] * 60) - sum(_minutes(parse_iso(b["start"]), parse_iso(b["end"]))
                                                    for b in rows if b["status"] == "accepted")
    lo, hi = p["min_block_min"], p["max_block_min"]
    ranked = rank_items(db.list_items(now), now, cfg)
    proposals = []

    def place(item_id, title, minutes):
        nonlocal slots, cap_left
        for i, (s, e) in enumerate(slots):
            if _minutes(s, e) >= lo:
                size = min(minutes, _minutes(s, e), cap_left)
                if size < lo:
                    return 0
                end = s + timedelta(minutes=size)
                slots[i] = (end, e)
                slots = [x for x in slots if x[1] > x[0]]
                cap_left -= size
                bid = db.run('INSERT INTO blocks (item_id, title, day, start, "end", status) VALUES (?,?,?,?,?,?)',
                             (item_id, title, day, iso(s), iso(end), "proposed")).lastrowid
                proposals.append({"id": bid, "item_id": item_id, "title": title, "start": iso(s), "end": iso(end)})
                return size
        return 0

    rejected_items = {b["item_id"] for b in rows if b["status"] == "rejected"}
    replies_today = any(b["item_id"] is None and b["status"] in ("accepted", "rejected") for b in rows)
    replies_open = sum(1 for s in ranked if s["kind"] in ("respond", "review"))
    if replies_open >= p["replies_threshold"] and not replies_today:
        place(None, REPLIES_TITLE, p["replies_block_min"])
    accepted_by_item = {}
    for b in rows:
        if b["status"] == "accepted" and b["item_id"]:
            accepted_by_item[b["item_id"]] = accepted_by_item.get(b["item_id"], 0) + \
                _minutes(parse_iso(b["start"]), parse_iso(b["end"]))
    logged = pomodoro.item_totals(db)
    for it in ranked:
        if it["kind"] != "do" or it["quadrant"] not in ("do_now", "schedule") or it["id"] in rejected_items:
            continue
        remaining = it["estimate_min"] - logged.get(it["id"], {"minutes": 0})["minutes"] \
            - accepted_by_item.get(it["id"], 0)
        first = True
        while remaining > 0 and cap_left >= lo:
            if remaining < lo:
                if not first:
                    break
                remaining = lo
            placed = place(it["id"], it["title"], min(remaining, hi))
            if not placed:
                break
            remaining -= placed
            first = False
    return proposals


def _block(db, block_id):
    r = db.one('SELECT * FROM blocks WHERE id=?', (block_id,))
    if r is None:
        raise PlannerError(f"no such block: {block_id}")
    return r


def accept(db, cfg, block_id, calendar, now):
    """Accept a proposed block; creates one calendar event when the calendar is enabled."""
    b = _block(db, block_id)
    if b["status"] == "accepted":
        return b
    if b["status"] != "proposed":
        raise PlannerError(f"block {block_id} is {b['status']}")
    event_id = None
    if calendar is not None:
        item = db.get_item(b["item_id"]) if b["item_id"] else None
        try:
            event_id = calendar.insert_block(b["title"], b["start"], b["end"], item["url"] if item else "")
        except Exception as e:
            raise PlannerError(f"calendar insert failed, block stays proposed: {type(e).__name__}: {e}") from None
    db.run("UPDATE blocks SET status='accepted', calendar_event_id=? WHERE id=?", (event_id, block_id))
    return _block(db, block_id)


def reject(db, block_id):
    _block(db, block_id)
    db.run("UPDATE blocks SET status='rejected' WHERE id=?", (block_id,))
    return _block(db, block_id)


def _esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n")
            .replace("\n", "\\n"))


def _fold(line):
    out, first = [], True
    while len(line) > 70:
        out.append(line[:70] if first else " " + line[:70])
        line, first = line[70:], False
    out.append(line if first else " " + line)
    return out


def _ics_time(ts):
    return parse_iso(ts).strftime("%Y%m%dT%H%M%SZ")


def to_ics(db, day, now):
    """Accepted blocks of `day` as an iCalendar string (CRLF line ends)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//workdesk//EN", "CALSCALE:GREGORIAN"]
    n = 0
    for b in _block_rows(db, day):
        if b["status"] != "accepted":
            continue
        n += 1
        lines += ["BEGIN:VEVENT", f"UID:workdesk-{b['id']}-{day}@workdesk.local", "DTSTAMP:" + _ics_time(iso(now)),
                  "DTSTART:" + _ics_time(b["start"]), "DTEND:" + _ics_time(b["end"]),
                  "SUMMARY:" + _esc("Focus: " + b["title"]), "DESCRIPTION:" + _esc(b["url"]),
                  "CLASS:PRIVATE", "TRANSP:OPAQUE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    folded = [seg for ln in lines for seg in _fold(ln)]
    return "\r\n".join(folded) + "\r\n", n
