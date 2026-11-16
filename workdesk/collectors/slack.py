"""Slack collector (user token): unanswered mentions and direct messages."""
from datetime import timedelta

from ..models import Item, iso, from_epoch
from .base import Collector, HttpError, parse_query


class SlackError(Exception):
    pass


def ts_key(ts):
    sec, _, micro = str(ts).partition(".")
    return int(sec), int(micro or 0)


def parse_match(raw):
    """One search.messages match -> flat dict. thread_ts comes from the field or the permalink."""
    ch = raw.get("channel") or {}
    link = raw.get("permalink") or ""
    return {"ts": raw["ts"], "text": raw.get("text") or "", "user": raw.get("user") or "",
            "username": raw.get("username") or "", "channel": ch.get("id") or "",
            "channel_name": ch.get("name") or "", "is_dm": bool(ch.get("is_im")),
            "thread_ts": raw.get("thread_ts") or parse_query(link).get("thread_ts"), "permalink": link}


def is_answered(messages, ts, me, reaction_done):
    """True when I posted after message `ts`, or reacted to it (if reaction_done)."""
    for m in messages:
        if m.get("user") == me and ts_key(m["ts"]) > ts_key(ts):
            return True
    if reaction_done:
        for m in messages:
            if m.get("ts") == ts:
                for r in m.get("reactions") or []:
                    if me in (r.get("users") or []):
                        return True
    return False


def make_item(mentions):
    """One Item for a group of unanswered mentions of the same thread."""
    mentions = sorted(mentions, key=lambda m: ts_key(m["ts"]))
    first, last = mentions[0], mentions[-1]
    text = " ".join(first["text"].split())[:140]
    title = f"@{first['username'] or first['channel_name']}: {text}" if first["is_dm"] else \
        f"#{first['channel_name']}: {text}"
    return Item(id=f"slack:{first['channel']}:{first['ts']}", source="slack", kind="respond", title=title,
                url=first["permalink"], requester=first["username"],
                status="direct message" if first["is_dm"] else "mention",
                created_at=iso(from_epoch(ts_key(first["ts"])[0])),
                updated_at=iso(from_epoch(ts_key(last["ts"])[0])),
                meta={"is_dm": first["is_dm"], "channel": first["channel_name"]})


class SlackCollector(Collector):
    name = "slack"

    def call(self, method, params=None):
        resp = self.http.request("GET", self.cfg["api_url"].rstrip("/") + "/" + method,
                                 headers={"Authorization": "Bearer " + self.token()}, params=params)
        if not resp.get("ok"):
            err = f"slack {method}: {resp.get('error', 'unknown_error')}"
            if resp.get("needed"):
                err += " (needed: " + resp["needed"] + ")"
            raise SlackError(err)
        return resp

    def collect(self, now):
        c = self.cfg
        me = self.call("auth.test")["user_id"]
        cutoff = int((now - timedelta(days=c["days"])).timestamp())
        after = (now - timedelta(days=c["days"])).strftime("%Y-%m-%d")
        found = {}
        for q in c["queries"]:
            q = q.replace("{user_id}", me) + f" after:{after}"
            resp = self.call("search.messages", {"query": q, "count": c["max_matches"], "sort": "timestamp",
                                                 "sort_dir": "desc"})
            for raw in (resp.get("messages") or {}).get("matches", [])[: c["max_matches"]]:
                m = parse_match(raw)
                if m["user"] == me or ts_key(m["ts"])[0] < cutoff:
                    continue
                found.setdefault((m["channel"], m["ts"]), m)
        groups = {}
        for m in found.values():
            if m["is_dm"] and not m["thread_ts"]:
                key = (m["channel"], m["ts"], "history")
            else:
                key = (m["channel"], m["thread_ts"] or m["ts"], "replies")
            groups.setdefault(key, []).append(m)
        items = []
        for (channel, root, how), mentions in groups.items():
            msgs = self.lookup(how, channel, root)
            open_ = [m for m in mentions
                     if msgs is None or not is_answered(msgs, m["ts"], me, c["reaction_counts_as_done"])]
            if open_:
                items.append(make_item(open_))
        return items

    def lookup(self, how, channel, root):
        """Messages used for the answered check, or None when the check fails (item is then kept)."""
        try:
            if how == "history":
                r = self.call("conversations.history", {"channel": channel, "oldest": root, "inclusive": "true",
                                                         "limit": 100})
            else:
                r = self.call("conversations.replies", {"channel": channel, "ts": root, "limit": 100})
            return r.get("messages") or []
        except (SlackError, HttpError):
            return None

    def check(self):
        r = self.call("auth.test")
        return "authenticated as " + (r.get("user") or r.get("user_id") or "unknown")
