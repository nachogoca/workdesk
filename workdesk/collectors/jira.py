"""Jira Cloud collector (REST v3, search/jql with nextPageToken paging)."""
from ..models import Item, norm_ts
from .base import Collector, b64

FIELDS = "summary,status,priority,duedate,created,updated,labels,reporter"
MAX_PAGES = 20


def parse_issue(raw, base_url, sprint_field, waiting_statuses):
    """One Jira issue JSON -> Item. The only place that knows Jira field names."""
    f = raw.get("fields") or {}
    status = (f.get("status") or {}).get("name") or ""
    sprints = f.get(sprint_field) or []
    return Item(
        id="jira:" + raw["key"], source="jira",
        kind="waiting" if status.lower() in {w.lower() for w in waiting_statuses} else "do",
        title=f.get("summary") or raw["key"], url=f"{base_url.rstrip('/')}/browse/{raw['key']}",
        requester=(f.get("reporter") or {}).get("displayName") or "", status=status,
        due=f.get("duedate") or None, created_at=norm_ts(f.get("created")), updated_at=norm_ts(f.get("updated")),
        labels=list(f.get("labels") or []),
        meta={"priority": (f.get("priority") or {}).get("name"),
              "in_sprint": any((s or {}).get("state") == "active" for s in sprints),
              "in_progress": ((f.get("status") or {}).get("statusCategory") or {}).get("key") == "indeterminate"})


class JiraCollector(Collector):
    name = "jira"

    def headers(self):
        tok = self.token()
        if self.cfg.get("auth") == "bearer":
            auth = "Bearer " + tok
        else:
            auth = "Basic " + b64(f"{self.cfg['email']}:{tok}")
        return {"Authorization": auth, "Accept": "application/json"}

    def collect(self, now):
        c = self.cfg
        hdr = self.headers()
        params = {"jql": c["jql"], "maxResults": c["page_size"], "fields": FIELDS + "," + c["sprint_field"]}
        url = c["base_url"].rstrip("/") + c["search_path"]
        items = []
        for _ in range(MAX_PAGES):
            resp = self.http.request("GET", url, headers=hdr, params=params)
            items += [parse_issue(i, c["base_url"], c["sprint_field"], c["waiting_statuses"])
                      for i in resp.get("issues", [])]
            tok = resp.get("nextPageToken")
            if not tok:
                break
            params = dict(params, nextPageToken=tok)
        return items

    def check(self):
        me = self.http.request("GET", self.cfg["base_url"].rstrip("/") + "/rest/api/3/myself",
                               headers=self.headers())
        return "authenticated as " + (me.get("displayName") or me.get("emailAddress") or "unknown")
