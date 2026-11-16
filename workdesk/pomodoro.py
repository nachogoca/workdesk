"""Pomodoro timer state, kept in the database so page and CLI share it."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import iso, parse_iso


def finalize(db, now):
    """Timers whose planned end has passed are closed as completed (ended_at = start + planned)."""
    for r in db.rows("SELECT * FROM pomodoros WHERE ended_at IS NULL"):
        end = parse_iso(r["started_at"]) + timedelta(minutes=r["planned_min"])
        if end <= now:
            db.run("UPDATE pomodoros SET ended_at=?, completed=1 WHERE id=?", (iso(end), r["id"]))


def running(db, now):
    finalize(db, now)
    return db.one("SELECT * FROM pomodoros WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")


def _local_day(ts, tz):
    return parse_iso(ts).astimezone(ZoneInfo(tz)).date().isoformat()


def completed_on(db, day, cfg):
    tz = cfg["general"]["timezone"]
    rows = db.rows("SELECT started_at FROM pomodoros WHERE kind='focus' AND completed=1")
    return sum(1 for r in rows if _local_day(r["started_at"], tz) == day)


def focus_minutes_of(r):
    if r["kind"] != "focus" or not r["ended_at"]:
        return 0
    if r["completed"]:
        return r["planned_min"]
    return int((parse_iso(r["ended_at"]) - parse_iso(r["started_at"])).total_seconds() // 60)


def today_totals(db, day, cfg):
    tz = cfg["general"]["timezone"]
    done, minutes = 0, 0
    for r in db.rows("SELECT * FROM pomodoros WHERE kind='focus' AND ended_at IS NOT NULL"):
        if _local_day(r["started_at"], tz) == day:
            done += 1 if r["completed"] else 0
            minutes += focus_minutes_of(r)
    return {"completed": done, "focus_min": minutes}


def item_totals(db):
    """item_id -> {"count": completed pomodoros, "minutes": focus minutes} (includes stopped-early time)."""
    out = {}
    for r in db.rows("SELECT * FROM pomodoros WHERE kind='focus' AND item_id IS NOT NULL AND ended_at IS NOT NULL"):
        o = out.setdefault(r["item_id"], {"count": 0, "minutes": 0})
        o["count"] += 1 if r["completed"] else 0
        o["minutes"] += focus_minutes_of(r)
    return out


def logged_minutes(db, item_id):
    return item_totals(db).get(item_id, {"minutes": 0})["minutes"]


def stop(db, now):
    r = running(db, now)
    if r is None:
        return None
    elapsed = (now - parse_iso(r["started_at"])).total_seconds()
    done = 1 if elapsed >= r["planned_min"] * 60 else 0
    db.run("UPDATE pomodoros SET ended_at=?, completed=? WHERE id=?", (iso(now), done, r["id"]))
    return r["id"]


def start(db, cfg, now, item_id=None, kind="focus"):
    if kind not in ("focus", "break"):
        raise ValueError("kind must be focus or break")
    if item_id is not None and not db.get_item(item_id):
        raise ValueError("no such item: " + str(item_id))
    stop(db, now)
    p = cfg["pomodoro"]
    if kind == "focus":
        planned = p["focus_min"]
    else:
        item_id = None
        n = completed_on(db, _local_day(iso(now), cfg["general"]["timezone"]), cfg)
        planned = p["long_break_min"] if n and n % p["long_break_after"] == 0 else p["break_min"]
    return db.run("INSERT INTO pomodoros (item_id, started_at, planned_min, kind) VALUES (?,?,?,?)",
                  (item_id, iso(now), planned, kind)).lastrowid


def status(db, cfg, now):
    r = running(db, now)
    day = _local_day(iso(now), cfg["general"]["timezone"])
    out = {"running": False, "id": None, "today": today_totals(db, day, cfg)}
    if r:
        end = parse_iso(r["started_at"]) + timedelta(minutes=r["planned_min"])
        item = db.get_item(r["item_id"]) if r["item_id"] else None
        out.update(running=True, id=r["id"], kind=r["kind"], item_id=r["item_id"],
                   item_title=item["title"] if item else None, started_at=r["started_at"], ends_at=iso(end),
                   planned_min=r["planned_min"], remaining_s=max(0, int((end - now).total_seconds())))
    return out
