import unittest

from workdesk.collectors.github import GitHubCollector, parse_notification, parse_search_item
from workdesk.config import DEFAULTS
from workdesk.demo import FixtureHttp, load_fixture
from tests.util import NOW

CFG = dict(DEFAULTS["github"], enabled=True)


class GitHubTests(unittest.TestCase):
    def test_parse_search_item(self):
        it = parse_search_item(load_fixture("github_assigned.json")["items"][1], "do")
        self.assertEqual((it.id, it.kind, it.requester, it.labels), ("github:acme/api#7", "do", "dave", ["bug"]))

    def test_parse_notification(self):
        notes = load_fixture("github_notifications.json")
        it = parse_notification(notes[0])
        self.assertEqual((it.id, it.kind, it.url), ("github:acme/api#45", "respond",
                                                    "https://github.com/acme/api/pull/45"))
        self.assertEqual(parse_notification(notes[1]).url, "https://github.com/acme/api/issues/7")
        self.assertIsNone(parse_notification(notes[2]))
        self.assertIsNone(parse_notification({"reason": "mention", "subject": {}, "repository": {}}))

    def test_collect_precedence(self):
        http = FixtureHttp()
        items = {i.id: i for i in GitHubCollector(CFG, http, {"GITHUB_TOKEN": "t"}).collect(NOW)}
        kinds = {k: v.kind for k, v in items.items()}
        self.assertEqual(kinds, {"github:acme/api#42": "review", "github:acme/web#43": "review",
                                 "github:acme/api#7": "respond", "github:acme/api#45": "respond",
                                 "github:acme/web#46": "waiting"})
        self.assertEqual(items["github:acme/api#45"].requester, "me-example")  # richer search record kept
        self.assertEqual(items["github:acme/api#7"].updated_at, "2026-01-15T10:00:00Z")  # latest activity
        self.assertEqual(len(http.calls), 4)
        self.assertEqual(http.calls[0]["params"]["per_page"], 50)

    def test_enterprise_api_url(self):
        http = FixtureHttp([(r".", {"items": []})])
        GitHubCollector(dict(CFG, api_url="https://ghe.example/api/v3"), http, {"GITHUB_TOKEN": "t"}).collect(NOW)
        self.assertTrue(http.calls[0]["url"].startswith("https://ghe.example/api/v3/search/issues"))


if __name__ == "__main__":
    unittest.main()
