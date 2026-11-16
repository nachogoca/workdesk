"""SQLite storage. Rows come back as dicts; labels/meta are JSON-decoded."""
import json
import os
import secrets
import sqlite3

from .models import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  url TEXT DEFAULT '', requester TEXT DEFAULT '', status TEXT DEFAULT '', due TEXT,
  created_at TEXT, updated_at TEXT, labels TEXT DEFAULT '[]', meta TEXT DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'open', state_changed_at TEXT, snooze_until TEXT,
  importance_override INTEGER, urgency_override INTEGER, estimate_min INTEGER NOT NULL DEFAULT 50,
  first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  ok INTEGER NOT NULL, error TEXT, count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pomodoros (
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, started_at TEXT NOT NULL, planned_min INTEGER NOT NULL,
  ended_at TEXT, completed INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'focus');
CREATE TABLE IF NOT EXISTS blocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, title TEXT, day TEXT NOT NULL,
  start TEXT NOT NULL, "end" TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', calendar_event_id TEXT);
CREATE TABLE IF NOT EXISTS aliases (
  day TEXT NOT NULL, alias TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY (day, alias));
"""

ITEM_FIELDS = ("title", "kind", "url", "requester", "status", "due", "created_at", "updated_at")
PREFIX = {"jira": "j", "github": "g", "slack": "s", "manual": "m"}


class DbError(Exception):
    pass


def _decode(row):
    d = dict(row)
    d["labels"] = json.loads(d.get("labels") or "[]")
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


class Database:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # generic helpers -------------------------------------------------
    def rows(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def run(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # items -------------------------------------------------------------
    def upsert_items(self, items, now):
        """Insert or refresh items from a source; local-only columns are kept."""
        n = iso(now)
        for it in items:
            old = self.one("SELECT state, state_changed_at, source FROM items WHERE id=?", (it.id,))
            vals = [getattr(it, f) for f in ITEM_FIELDS] + [json.dumps(it.labels), json.dumps(it.meta)]
            if old is None:
                self.conn.execute(
                    "INSERT INTO items (id, source, title, kind, url, requester, status, due, created_at, updated_at,"
                    " labels, meta, state, state_changed_at, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)",
                    [it.id, it.source, vals[0], vals[1], vals[2], vals[3], vals[4], vals[5], vals[6], vals[7],
                     vals[8], vals[9], n, n, n])
                continue
            state, changed = old["state"], old["state_changed_at"]
            reopen = state == "closed" or (
                state == "done" and old["source"] != "manual" and it.updated_at and changed
                and it.updated_at > changed)
            self.conn.execute(
                "UPDATE items SET title=?, kind=?, url=?, requester=?, status=?, due=?, created_at=?, updated_at=?,"
                " labels=?, meta=?, last_seen=? WHERE id=?", vals + [n, it.id])
            if reopen:
                self.conn.execute("UPDATE items SET state='open', state_changed_at=?, snooze_until=NULL WHERE id=?",
                                  (n, it.id))
        self.conn.commit()

    def close_missing(self, source, seen_ids, now):
        seen = set(seen_ids)
        rows = self.rows("SELECT id FROM items WHERE source=? AND state!='closed'", (source,))
        gone = [r["id"] for r in rows if r["id"] not in seen]
        for i in gone:
            self.conn.execute("UPDATE items SET state='closed', state_changed_at=? WHERE id=?", (iso(now), i))
        self.conn.commit()
        return len(gone)

    def add_manual(self, title, now, due=None, kind="do", url="", importance=None, estimate=50, requester=""):
        item_id = "manual:" + secrets.token_hex(4)
        n = iso(now)
        self.run(
            "INSERT INTO items (id, source, kind, title, url, requester, status, due, created_at, updated_at, labels, meta,"
            " state, state_changed_at, importance_override, estimate_min, first_seen, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,'[]','{}','open',?,?,?,?,?)",
            (item_id, "manual", kind, title, url or "", requester, "manual", due, n, n, n, importance, estimate, n, n))
        return item_id

    def expire_snoozes(self, now):
        self.run("UPDATE items SET state='open', snooze_until=NULL WHERE state='snoozed' AND snooze_until<=?",
                 (iso(now),))

    def get_item(self, item_id):
        r = self.one("SELECT * FROM items WHERE id=?", (item_id,))
        return _decode(r) if r else None

    def list_items(self, now, states=("open",)):
        self.expire_snoozes(now)
        q = ",".join("?" * len(states))
        return [_decode(r) for r in
                self.conn.execute(f"SELECT * FROM items WHERE state IN ({q}) ORDER BY id", states).fetchall()]

    def set_state(self, item_id, state, now, snooze_until=None):
        if not self.get_item(item_id):
            raise DbError("no such item: " + item_id)
        self.run("UPDATE items SET state=?, state_changed_at=?, snooze_until=? WHERE id=?",
                 (state, iso(now), snooze_until, item_id))

    def set_override(self, item_id, field, value):
        if field not in ("importance", "urgency"):
            raise DbError("field must be importance or urgency")
        if not self.get_item(item_id):
            raise DbError("no such item: " + item_id)
        self.run(f"UPDATE items SET {field}_override=? WHERE id=?", (value, item_id))

    def set_estimate(self, item_id, minutes):
        if not self.get_item(item_id):
            raise DbError("no such item: " + item_id)
        minutes = int(minutes)
        if minutes < 1 or minutes > 1440:
            raise DbError("estimate must be 1..1440 minutes")
        self.run("UPDATE items SET estimate_min=? WHERE id=?", (minutes, item_id))

    # sync log ----------------------------------------------------------
    def log_sync(self, source, ok, error, count, started, finished):
        self.run("INSERT INTO sync_log (source, started_at, finished_at, ok, error, count) VALUES (?,?,?,?,?,?)",
                 (source, iso(started), iso(finished), 1 if ok else 0, error, count))

    def last_syncs(self):
        """source -> latest log row (any outcome) plus last_ok_at."""
        out = {}
        for r in self.rows("SELECT * FROM sync_log ORDER BY id"):
            o = out.setdefault(r["source"], {})
            o.update(at=r["finished_at"], ok=bool(r["ok"]), error=r["error"], count=r["count"])
            if r["ok"]:
                o["last_ok_at"] = r["finished_at"]
        return out

    def last_sync_at(self):
        r = self.one("SELECT MAX(finished_at) AS m FROM sync_log")
        return r["m"] if r else None

    # aliases -------------------------------------------------------------
    def alias_for(self, item_id, day):
        """Stable short alias for an item for one day (j1, g2, s1, m1)."""
        self.run("DELETE FROM aliases WHERE day<?", (day,))
        r = self.one("SELECT alias FROM aliases WHERE day=? AND item_id=?", (day, item_id))
        if r:
            return r["alias"]
        pfx = PREFIX.get(item_id.split(":", 1)[0], "x")
        nums = [int(a["alias"][1:]) for a in self.rows("SELECT alias FROM aliases WHERE day=? AND alias LIKE ?",
                                                        (day, pfx + "%"))]
        alias = f"{pfx}{max(nums, default=0) + 1}"
        self.run("INSERT INTO aliases (day, alias, item_id) VALUES (?,?,?)", (day, alias, item_id))
        return alias

    def resolve(self, ref, day):
        r = self.one("SELECT item_id FROM aliases WHERE day=? AND alias=?", (day, ref))
        if r:
            return r["item_id"]
        if self.get_item(ref):
            return ref
        raise DbError(f"unknown item reference: {ref}")
