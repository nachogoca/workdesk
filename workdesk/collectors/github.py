"""GitHub collector: search API plus participating notifications."""
import re

from ..models import Item, norm_ts
from .base import Collector

SEARCHES = (("is:open is:pr review-requested:@me", "review"),
            ("is:open assignee:@me", "do"),
            ("is:open is:pr author:@me", "waiting"))
RESPOND_REASONS = ("mention", "team_mention", "author", "comment")
PRECEDENCE = {"review": 0, "respond": 1, "do": 2, "waiting": 3}


def parse_search_item(raw, kind):
    """One search/issues result -> Item."""
    repo = raw["repository_url"].split("/repos/", 1)[1]
    return Item(
        id=f"github:{repo}#{raw['number']}", source="github", kind=kind, title=raw.get("title") or "",
        url=raw.get("html_url") or "", requester=(raw.get("user") or {}).get("login") or "",
        status=("PR " if raw.get("pull_request") else "issue ") + (raw.get("state") or "open"),
        created_at=norm_ts(raw.get("created_at")), updated_at=norm_ts(raw.get("updated_at")),
        labels=[l.get("name", "") for l in raw.get("labels") or []],
        meta={"is_pr": bool(raw.get("pull_request"))})


def parse_notification(raw):
    """One notification -> Item (kind respond), or None for other reasons / missing subject url."""
    if raw.get("reason") not in RESPOND_REASONS:
        return None
    subj = raw.get("subject") or {}
    m = re.search(r"/repos/([^/]+/[^/]+)/(pulls|issues)/(\d+)$", subj.get("url") or "")
    repo = (raw.get("repository") or {}).get("html_url")
    if not m or not repo:
        return None
    path = "pull" if m.group(2) == "pulls" else "issues"
    return Item(
        id=f"github:{m.group(1)}#{m.group(3)}", source="github", kind="respond", title=subj.get("title") or "",
        url=f"{repo}/{path}/{m.group(3)}", status=raw["reason"], created_at=norm_ts(raw.get("updated_at")),
        updated_at=norm_ts(raw.get("updated_at")), meta={"is_pr": path == "pull"})


def merge(into, item, from_search):
    """Merge a duplicate sighting: best kind by precedence, latest activity, richer record kept."""
    cur = into.get(item.id)
    if cur is None:
        into[item.id] = item
        return
    kind = min(cur.kind, item.kind, key=PRECEDENCE.get)
    newest = max(cur.updated_at or "", item.updated_at or "") or None
    base = item if (from_search and not cur.requester and item.requester) else cur
    base.kind, base.updated_at = kind, newest
    into[item.id] = base


class GitHubCollector(Collector):
    name = "github"

    def headers(self):
        return {"Authorization": "Bearer " + self.token(), "Accept": "application/vnd.github+json"}

    def collect(self, now):
        api = self.cfg["api_url"].rstrip("/")
        hdr = self.headers()
        found = {}
        for q, kind in SEARCHES:
            resp = self.http.request("GET", api + "/search/issues", headers=hdr, params={"q": q, "per_page": 50})
            for raw in resp.get("items", []):
                merge(found, parse_search_item(raw, kind), True)
        notes = self.http.request("GET", api + "/notifications", headers=hdr, params={"participating": "true"})
        for raw in notes if isinstance(notes, list) else []:
            it = parse_notification(raw)
            if it:
                merge(found, it, False)
        return list(found.values())

    def check(self):
        me = self.http.request("GET", self.cfg["api_url"].rstrip("/") + "/user", headers=self.headers())
        return "authenticated as " + (me.get("login") or "unknown")
