import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import timedelta

from workdesk import brief
from workdesk.cli import main
from workdesk.demo import FixtureHttp, routes
from workdesk.collectors.base import HttpError
from workdesk.sync import run_sync
from tests.util import NOW, cfg, mem_db, item, ago

ENV = {"JIRA_TOKEN": "t", "GITHUB_TOKEN": "t", "SLACK_TOKEN": "t"}


def conf():
    return cfg(jira__enabled=True, github__enabled=True, slack__enabled=True)


def synced_db():
    db = mem_db()
    run_sync(db, conf(), FixtureHttp(), ENV, NOW)
    return db


class BriefTests(unittest.TestCase):
    def test_limits_and_sections(self):
        db = synced_db()
        data, text = brief.make(db, conf(), NOW, None)
        self.assertLessEqual(text.count("\n") + 1, 30)
        self.assertLessEqual(len(text), 1800)
        order = [text.index(k) for k in ("TOP", "REPLY", "NUDGE", "CALENDAR", "YESTERDAY")]
        self.assertEqual(order, sorted(order))
        self.assertIn("Thu 2026-01-15", text)
        self.assertEqual(len(data["top"]), 5)
        self.assertEqual(len(data["reply"]["oldest"]), 3)
        self.assertEqual(data["reply"]["count"], 7)
        self.assertTrue(data["nudge"])

    def test_limits_with_huge_titles_and_many_items(self):
        db = mem_db()
        for i in range(60):
            db.upsert_items([item(f"github:o/r#{i}", source="github", kind="review", title="very long title " * 20,
                                  updated_at=ago(hours=i + 1))], NOW)
            db.upsert_items([item(f"jira:W{i}", kind="waiting", title="w" * 300, updated_at=ago(days=9))], NOW)
        data, text = brief.make(db, cfg(), NOW, None)
        self.assertLessEqual(text.count("\n") + 1, 30)
        self.assertLessEqual(len(text), 1800)
        for t in data["top"]:
            self.assertLessEqual(len(t["title"]), 60)

    def test_json_same_content(self):
        db = synced_db()
        data, text = brief.make(db, conf(), NOW, None)
        j = json.loads(brief.render_json(data))
        self.assertEqual(j, data)
        for t in data["top"]:
            self.assertIn(t["alias"] + " ", text)
        self.assertNotIn('": "', brief.render_json(data))

    def test_alias_stability_within_day(self):
        db = synced_db()
        d1, _ = brief.make(db, conf(), NOW, None)
        db.upsert_items([item("jira:NEW", kind="do", due="2026-01-14", meta={"priority": "Highest"})], NOW)
        d2, _ = brief.make(db, conf(), NOW + timedelta(hours=1), None)
        a1 = {t["title"]: t["alias"] for t in d1["top"]}
        a2 = {t["title"]: t["alias"] for t in d2["top"]}
        for title, alias in a1.items():
            if title in a2:
                self.assertEqual(a2[title], alias)
        new = db.alias_for("jira:NEW", "2026-01-15")
        self.assertNotIn(new, a1.values())
        db.alias_for("jira:NEW", "2026-01-16")
        self.assertEqual(db.rows("SELECT * FROM aliases WHERE day='2026-01-15'"), [])

    def test_failed_sources_listed(self):
        db = mem_db()
        table = [(r"/rest/api/3/search/jql", HttpError(401, "https://acme/x"))] + routes()
        run_sync(db, conf(), FixtureHttp(table), ENV, NOW)
        data, text = brief.make(db, conf(), NOW, None)
        self.assertEqual(data["failed"][0]["source"], "jira")
        self.assertIn("SYNC FAILED jira: HttpError: HTTP 401", text)

    def test_yesterday_pomodoros(self):
        from workdesk import pomodoro
        db = mem_db()
        db.upsert_items([item("jira:A")], NOW)
        pomodoro.start(db, cfg(), NOW - timedelta(days=1), item_id="jira:A")
        data, text = brief.make(db, cfg(), NOW, None)
        self.assertEqual(data["yesterday"], 1)
        self.assertIn("YESTERDAY 1 pomodoros", text)

    def test_blocks_listed_with_ids(self):
        from workdesk import planner
        db = mem_db()
        db.upsert_items([item("jira:A", due="2026-01-15", meta={"priority": "High"})], NOW)
        planner.plan(db, cfg(), "2026-01-15", None, NOW)
        data, text = brief.make(db, cfg(), NOW, None)
        self.assertEqual(len(data["blocks"]), 1)
        self.assertRegex(text, r"block 1 \d\d:\d\d-\d\d:\d\d")

    def test_show_text(self):
        db = synced_db()
        text = brief.show_text(db, conf(), NOW, "jira:MLP-101")
        self.assertIn("urgency:", text)
        self.assertIn("url: https://acme.atlassian.net/browse/MLP-101", text)


class BriefCliTests(unittest.TestCase):
    def test_demo_today_and_show(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"WORKDESK_DEMO_DB": os.path.join(d, "demo.db"), "WORKDESK_CONFIG": os.path.join(d, "x")}

            def run(*a):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(list(a), env=env, now=NOW)
                return code, out.getvalue()

            self.assertEqual(run("demo", "--no-serve")[0], 0)
            code, text = run("--demo", "today", "--no-sync")
            self.assertEqual(code, 0)
            self.assertLessEqual(len(text.strip().split("\n")), 30)
            self.assertLessEqual(len(text), 1800)
            alias = text.split("\nTOP\n")[1].split()[0]
            code, detail = run("--demo", "show", alias)
            self.assertEqual(code, 0)
            self.assertIn("rank/quadrant:", detail)
            code, js = run("--demo", "today", "--no-sync", "--json")
            self.assertIn("top", json.loads(js))
            self.assertEqual(run("--demo", "show", "zz99")[0], 1)


if __name__ == "__main__":
    unittest.main()
