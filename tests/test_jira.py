import unittest

from workdesk.collectors.base import ConfigError, HttpError
from workdesk.collectors.jira import JiraCollector, parse_issue
from workdesk.config import DEFAULTS
from workdesk.demo import FixtureHttp, load_fixture
from tests.util import NOW

CFG = dict(DEFAULTS["jira"], enabled=True)


class JiraTests(unittest.TestCase):
    def test_parse_issue(self):
        raw = load_fixture("jira_search_1.json")["issues"][0]
        it = parse_issue(raw, "https://acme.atlassian.net/", "customfield_10020", ["Blocked"])
        self.assertEqual((it.id, it.kind, it.source), ("jira:MLP-101", "do", "jira"))
        self.assertEqual(it.url, "https://acme.atlassian.net/browse/MLP-101")
        self.assertEqual(it.requester, "Dana Example")
        self.assertEqual(it.updated_at, "2026-01-15T09:00:00Z")
        self.assertEqual(it.meta, {"priority": "High", "in_sprint": True, "in_progress": True})

    def test_collect_pages_and_kinds(self):
        http = FixtureHttp()
        items = {i.id: i for i in JiraCollector(CFG, http, {"JIRA_TOKEN": "t"}).collect(NOW)}
        self.assertEqual(sorted(items), ["jira:MLP-101", "jira:MLP-102", "jira:MLP-103", "jira:MLP-104"])
        self.assertEqual(items["jira:MLP-102"].kind, "waiting")
        self.assertEqual(items["jira:MLP-103"].due, "2026-01-16")
        self.assertEqual(items["jira:MLP-103"].labels, ["incident"])
        self.assertFalse(items["jira:MLP-104"].meta["in_sprint"])
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(http.calls[1]["params"]["nextPageToken"], "tok2")

    def test_auth_headers(self):
        http = FixtureHttp()
        JiraCollector(CFG, http, {"JIRA_TOKEN": "t"}).collect(NOW)
        self.assertTrue(http.calls[0]["headers"]["Authorization"].startswith("Basic "))
        http = FixtureHttp()
        JiraCollector(dict(CFG, auth="bearer"), http, {"JIRA_TOKEN": "t"}).collect(NOW)
        self.assertEqual(http.calls[0]["headers"]["Authorization"], "Bearer t")

    def test_missing_token(self):
        with self.assertRaisesRegex(ConfigError, "missing env var JIRA_TOKEN"):
            JiraCollector(CFG, FixtureHttp(), {}).collect(NOW)

    def test_check_and_error_message_is_safe(self):
        self.assertIn("Me Example", JiraCollector(CFG, FixtureHttp(), {"JIRA_TOKEN": "t"}).check())
        e = HttpError(401, "https://x.example/a?token=secret")
        self.assertNotIn("secret", str(e))


if __name__ == "__main__":
    unittest.main()
