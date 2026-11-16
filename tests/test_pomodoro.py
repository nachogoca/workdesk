import contextlib
import io
import os
import tempfile
import unittest
from datetime import timedelta

from workdesk import pomodoro as pm
from workdesk.cli import main
from tests.util import NOW, cfg, mem_db, item

C = cfg()


def m(n):
    return NOW + timedelta(minutes=n)


class PomodoroTests(unittest.TestCase):
    def setUp(self):
        self.db = mem_db()
        self.db.upsert_items([item("jira:A"), item("jira:B")], NOW)

    def test_start_and_status(self):
        pm.start(self.db, C, NOW, item_id="jira:A")
        st = pm.status(self.db, C, m(10))
        self.assertTrue(st["running"])
        self.assertEqual((st["kind"], st["item_id"], st["remaining_s"]), ("focus", "jira:A", 15 * 60))
        self.assertEqual(st["ends_at"], "2026-01-15T12:25:00Z")

    def test_completion_is_finalised_by_status(self):
        pm.start(self.db, C, NOW, item_id="jira:A")
        st = pm.status(self.db, C, m(30))
        self.assertFalse(st["running"])
        self.assertEqual(st["today"], {"completed": 1, "focus_min": 25})
        row = self.db.one("SELECT * FROM pomodoros")
        self.assertEqual((row["completed"], row["ended_at"]), (1, "2026-01-15T12:25:00Z"))

    def test_stop_early_does_not_complete(self):
        pm.start(self.db, C, NOW, item_id="jira:A")
        pm.stop(self.db, m(10))
        row = self.db.one("SELECT * FROM pomodoros")
        self.assertEqual(row["completed"], 0)
        self.assertEqual(pm.item_totals(self.db)["jira:A"], {"count": 0, "minutes": 10})
        self.assertEqual(pm.today_totals(self.db, "2026-01-15", C), {"completed": 0, "focus_min": 10})

    def test_start_stops_running_timer(self):
        pm.start(self.db, C, NOW, item_id="jira:A")
        pm.start(self.db, C, m(5), item_id="jira:B")
        rows = self.db.rows("SELECT * FROM pomodoros ORDER BY id")
        self.assertEqual([r["ended_at"] is not None for r in rows], [True, False])
        self.assertEqual(pm.status(self.db, C, m(6))["item_id"], "jira:B")

    def test_item_totals(self):
        for i, iid in enumerate(["jira:A", "jira:A", "jira:B"]):
            pm.start(self.db, C, m(i * 30), item_id=iid)
        pm.status(self.db, C, m(200))
        t = pm.item_totals(self.db)
        self.assertEqual(t["jira:A"], {"count": 2, "minutes": 50})
        self.assertEqual(t["jira:B"], {"count": 1, "minutes": 25})
        self.assertEqual(pm.logged_minutes(self.db, "jira:A"), 50)

    def test_break_lengths(self):
        for i in range(4):
            pm.start(self.db, C, m(i * 40), item_id="jira:A")
            if i < 3:
                pm.stop(self.db, m(i * 40 + 25))
                pm.start(self.db, C, m(i * 40 + 26), kind="break")
                self.assertEqual(self.db.one("SELECT planned_min FROM pomodoros ORDER BY id DESC")["planned_min"], 5)
        pm.stop(self.db, m(3 * 40 + 25))
        pm.start(self.db, C, m(3 * 40 + 26), kind="break")
        self.assertEqual(self.db.one("SELECT planned_min FROM pomodoros ORDER BY id DESC")["planned_min"], 15)

    def test_validation(self):
        with self.assertRaises(ValueError):
            pm.start(self.db, C, NOW, item_id="jira:NOPE")

    def test_configurable(self):
        c = cfg(pomodoro__focus_min=10)
        pm.start(self.db, c, NOW)
        self.assertEqual(pm.status(self.db, c, NOW)["planned_min"], 10)


class PomoCliTests(unittest.TestCase):
    def test_cli_start_stop_status(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"WORKDESK_DB": os.path.join(d, "w.db"), "WORKDESK_CONFIG": os.path.join(d, "x.toml")}

            def run(*a, now=NOW):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(list(a), env=env, now=now)
                return code, out.getvalue()

            run("add", "Deep work")
            self.assertIn("focus 25:00 left on Deep work", run("pomo", "start", "m1")[1])
            self.assertIn("focus 20:00 left", run("pomo", "status", now=m(5))[1])
            self.assertIn("idle; today: 0 pomodoros, 5 min", run("pomo", "stop", now=m(5))[1])
            self.assertIn("no timer running", run("pomo", "stop", now=m(6))[1])


if __name__ == "__main__":
    unittest.main()
