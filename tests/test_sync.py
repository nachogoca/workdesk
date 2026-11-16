import unittest

from workdesk.collectors.base import HttpError
from workdesk.demo import FixtureHttp, routes
from workdesk.sync import run_sync, is_stale
from tests.util import NOW, cfg, mem_db
from datetime import timedelta

ENV = {"JIRA_TOKEN": "t", "GITHUB_TOKEN": "t", "SLACK_TOKEN": "t"}


def conf():
    return cfg(jira__enabled=True, github__enabled=True, slack__enabled=True)


class SyncTests(unittest.TestCase):
    def test_sync_all(self):
        db = mem_db()
        res = run_sync(db, conf(), FixtureHttp(), ENV, NOW)
        self.assertEqual({k: v["ok"] for k, v in res.items()}, {"jira": True, "github": True, "slack": True})
        self.assertEqual(len(db.list_items(NOW)), 4 + 5 + 3)

    def test_failing_collector_keeps_items_and_records_error(self):
        db = mem_db()
        run_sync(db, conf(), FixtureHttp(), ENV, NOW)
        later = NOW + timedelta(hours=1)
        table = [(r"/rest/api/3/search/jql", HttpError(500, "https://acme.atlassian.net/x?secret=1"))] + routes()
        res = run_sync(db, conf(), FixtureHttp(table), ENV, later)
        self.assertFalse(res["jira"]["ok"])
        self.assertTrue(res["github"]["ok"] and res["slack"]["ok"])
        self.assertEqual(len(db.list_items(later, ("open",))), 12)  # nothing closed
        self.assertIn("HTTP 500", db.last_syncs()["jira"]["error"])
        self.assertNotIn("secret", db.last_syncs()["jira"]["error"])

    def test_missing_item_closed_only_on_success(self):
        db = mem_db()
        run_sync(db, conf(), FixtureHttp(), ENV, NOW)
        table = [(r"/rest/api/3/search/jql", {"issues": []})] + routes()
        run_sync(db, conf(), FixtureHttp(table), ENV, NOW)
        self.assertEqual(len(db.list_items(NOW, ("closed",))), 4)

    def test_missing_token_is_failure_not_crash(self):
        db = mem_db()
        res = run_sync(db, conf(), FixtureHttp(), {}, NOW)
        self.assertIn("missing env var", res["jira"]["error"])

    def test_stale(self):
        db = mem_db()
        self.assertTrue(is_stale(db, NOW, 15))
        run_sync(db, conf(), FixtureHttp(), ENV, NOW)
        self.assertFalse(is_stale(db, NOW + timedelta(minutes=10), 15))
        self.assertTrue(is_stale(db, NOW + timedelta(minutes=16), 15))


if __name__ == "__main__":
    unittest.main()
