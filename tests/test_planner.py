import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

from workdesk import planner as pl, pomodoro
from workdesk.collectors.base import HttpError
from workdesk.collectors.gcal import GoogleCalendar, MemoryTokenStore
from workdesk.config import DEFAULTS
from workdesk.demo import FixtureHttp, routes
from workdesk.models import parse_iso
from tests.util import NOW, cfg, mem_db, item, ago

DAY = "2026-01-15"  # Thursday; Berlin = UTC+1
MORNING = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)  # 08:00 local
C = cfg()


def z(h, m=0, day=15):
    return datetime(2026, 1, day, h, m, tzinfo=timezone.utc)


def dos(db, n=1, estimate=60, **kw):
    for i in range(n):
        db.upsert_items([item(f"jira:D{i}", kind="do", due=DAY, meta={"priority": "High"}, **kw)], MORNING)
        db.set_estimate(f"jira:D{i}", estimate)


def make_cal(http):
    return GoogleCalendar(dict(DEFAULTS["calendar"], enabled=True), http, {"GOOGLE_CLIENT_ID": "a",
                          "GOOGLE_CLIENT_SECRET": "b"}, MemoryTokenStore({"refresh_token": "r"}))


class SlotTests(unittest.TestCase):
    def test_plain_day(self):
        self.assertEqual(pl.free_slots(DAY, C, [], MORNING), [(z(8, 30), z(11, 30)), (z(12, 30), z(17))])

    def test_meeting_buffer(self):
        s = pl.free_slots(DAY, C, [(z(13), z(14))], MORNING)
        self.assertEqual(s, [(z(8, 30), z(11, 30)), (z(12, 30), z(12, 55)), (z(14, 5), z(17))])

    def test_accepted_blocks_have_no_buffer(self):
        s = pl.free_slots(DAY, C, [], MORNING, [(z(13), z(14))])
        self.assertIn((z(12, 30), z(13)), s)
        self.assertIn((z(14), z(17)), s)

    def test_weekend_empty(self):
        self.assertEqual(pl.free_slots("2026-01-17", C, [], MORNING), [])

    def test_nothing_before_now_rounded_up(self):
        s = pl.free_slots(DAY, C, [], z(10, 2))
        self.assertEqual(s[0], (z(10, 5), z(11, 30)))
        self.assertEqual(pl.free_slots(DAY, C, [], z(18)), [])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.db = mem_db()

    def blocks(self, status="proposed"):
        return [(parse_iso(b["start"]), parse_iso(b["end"]), b) for b in pl.day_view(self.db, C, DAY, None, MORNING)
                ["blocks"] if b["status"] == status]

    def test_blocks_respect_limits_lunch_and_order(self):
        dos(self.db, 1, estimate=120)
        pl.plan(self.db, C, DAY, None, MORNING)
        bl = self.blocks()
        self.assertEqual(sum((e - s).seconds // 60 for s, e, _ in bl), 120)
        for s, e, _ in bl:
            self.assertTrue(25 <= (e - s).seconds // 60 <= 90)
            self.assertTrue(e <= z(11, 30) or s >= z(12, 30))
        self.assertEqual(bl[0][0], z(8, 30))

    def test_split_in_chunks_when_slot_is_small(self):
        dos(self.db, 1, estimate=100)
        cal_http = FixtureHttp([(r"oauth2", {"access_token": "t"}), (r"/events\?", {"items": [
            {"id": "m", "summary": "m", "start": {"dateTime": "2026-01-15T09:35:00Z"}, "end": {"dateTime": "2026-01-15T11:00:00Z"}}]})])
        pl.plan(self.db, C, DAY, make_cal(cal_http), MORNING)
        spans = [(s, e) for s, e, _ in self.blocks()]
        # meeting 09:35-11:00 (+5 buffer) leaves 08:30-09:30, 11:05-11:30 (25 min) and 12:30-17:00:
        # 60 min, then a 25 min chunk; the 15 min leftover is below the minimum and dropped
        self.assertEqual(spans, [(z(8, 30), z(9, 30)), (z(11, 5), z(11, 30))])

    def test_respects_meetings_with_buffer(self):
        dos(self.db, 2, estimate=90)
        cal_http = FixtureHttp(routes())
        pl.plan(self.db, C, DAY, make_cal(cal_http), MORNING)
        for s, e, _ in self.blocks():
            for ms, me in ((z(9), z(9, 15)), (z(13), z(14))):
                self.assertTrue(e <= ms - timedelta(minutes=5) or s >= me + timedelta(minutes=5), (s, e))

    def test_daily_cap(self):
        dos(self.db, 4, estimate=120)
        pl.plan(self.db, C, DAY, None, MORNING)
        self.assertEqual(sum((e - s).seconds // 60 for s, e, _ in self.blocks()), 240)

    def test_cap_counts_accepted_and_replies(self):
        dos(self.db, 3, estimate=120)
        for i in range(3):
            self.db.upsert_items([item(f"github:o/r#{i}", source="github", kind="review")], MORNING)
        pl.plan(self.db, C, DAY, None, MORNING)
        bl = self.blocks()
        self.assertEqual(bl[0][2]["title"], "Replies and reviews")
        self.assertEqual((bl[0][1] - bl[0][0]).seconds // 60, 30)
        self.assertEqual(sum((e - s).seconds // 60 for s, e, _ in bl), 240)

    def test_no_replies_block_below_threshold(self):
        for i in range(2):
            self.db.upsert_items([item(f"github:o/r#{i}", source="github", kind="review")], MORNING)
        pl.plan(self.db, C, DAY, None, MORNING)
        self.assertEqual(self.blocks(), [])

    def test_only_do_items_in_top_quadrants(self):
        self.db.upsert_items([item("jira:LOW", kind="do", meta={"priority": "Low"}),
                              item("jira:W", kind="waiting", due=DAY)], MORNING)
        pl.plan(self.db, C, DAY, None, MORNING)
        self.assertEqual(self.blocks(), [])

    def test_remaining_effort_uses_logged_pomodoros(self):
        dos(self.db, 1, estimate=50)
        pomodoro.start(self.db, C, MORNING - timedelta(hours=1), item_id="jira:D0")
        pomodoro.stop(self.db, MORNING - timedelta(minutes=35))  # 25 min completed
        pl.plan(self.db, C, DAY, None, MORNING)
        bl = self.blocks()
        self.assertEqual(len(bl), 1)
        self.assertEqual((bl[0][1] - bl[0][0]).seconds // 60, 25)

    def test_small_remainder_rounded_up_leftover_dropped(self):
        dos(self.db, 1, estimate=10)
        pl.plan(self.db, C, DAY, None, MORNING)
        self.assertEqual([(e - s).seconds // 60 for s, e, _ in self.blocks()], [25])
        self.db.run("DELETE FROM blocks")
        self.db.set_estimate("jira:D0", 100)
        pl.plan(self.db, C, DAY, None, MORNING)
        self.assertEqual([(e - s).seconds // 60 for s, e, _ in self.blocks()], [90])

    def test_replan_replaces_only_proposed(self):
        dos(self.db, 2, estimate=60)
        pl.plan(self.db, C, DAY, None, MORNING)
        first = self.blocks()
        pl.accept(self.db, C, first[0][2]["id"], None, MORNING)
        pl.reject(self.db, first[1][2]["id"])
        pl.plan(self.db, C, DAY, None, MORNING)
        self.assertEqual(len(self.blocks("accepted")), 1)
        self.assertEqual(len(self.blocks("rejected")), 1)
        self.assertEqual(self.blocks(), [])  # accepted item covered, rejected item not proposed again

    def test_weekend_plans_nothing(self):
        dos(self.db, 1)
        self.assertEqual(pl.plan(self.db, C, "2026-01-17", None, MORNING), [])

    def test_accept_inserts_once(self):
        dos(self.db, 1, estimate=60)
        http = FixtureHttp(routes())
        cal = make_cal(http)
        pl.plan(self.db, C, DAY, cal, MORNING)
        bid = self.blocks()[0][2]["id"]
        b = pl.accept(self.db, C, bid, cal, MORNING)
        pl.accept(self.db, C, bid, cal, MORNING)
        inserts = [c for c in http.calls if c["method"] == "POST" and c["json"]]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(b["status"], "accepted")
        self.assertTrue(b["calendar_event_id"].startswith("demo-event-"))
        self.assertEqual(inserts[0]["json"]["summary"], "Focus: t")

    def test_failed_insert_keeps_block_proposed(self):
        dos(self.db, 1, estimate=60)
        http = FixtureHttp([(r"oauth2", {"access_token": "t"}), (r"/events\?", {"items": []}),
                            (r"^POST .*/events$", HttpError(500, "https://x/events"))])
        cal = make_cal(http)
        pl.plan(self.db, C, DAY, cal, MORNING)
        bid = self.blocks()[0][2]["id"]
        with self.assertRaises(pl.PlannerError):
            pl.accept(self.db, C, bid, cal, MORNING)
        self.assertEqual(self.db.one("SELECT status FROM blocks WHERE id=?", (bid,))["status"], "proposed")

    def test_calendar_error_falls_back_to_working_hours(self):
        dos(self.db, 1)
        http = FixtureHttp([(r".", HttpError(401, "https://x"))])
        view = pl.day_view(self.db, C, DAY, make_cal(http), MORNING)
        self.assertIn("HTTP 401", view["calendar_error"])
        self.assertTrue(pl.plan(self.db, C, DAY, make_cal(http), MORNING))

    def test_day_view_numbers(self):
        v = pl.day_view(self.db, C, DAY, make_cal(FixtureHttp(routes())), MORNING)
        self.assertEqual(len(v["meetings"]), 2)
        self.assertEqual(v["meeting_min"], 75)
        self.assertEqual(v["free_focus_min"], 240)  # capped
        self.assertTrue(v["calendar_enabled"])

    def test_ics(self):
        dos(self.db, 1, estimate=60)
        self.db.run("UPDATE items SET title='Fix; the, thing' WHERE id='jira:D0'")
        pl.plan(self.db, C, DAY, None, MORNING)
        pl.accept(self.db, C, self.blocks()[0][2]["id"], None, MORNING)
        text, n = pl.to_ics(self.db, DAY, MORNING)
        self.assertEqual(n, 1)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n\n", text.replace("\r\n", ""))
        for needle in ("CLASS:PRIVATE", "TRANSP:OPAQUE", r"SUMMARY:Focus: Fix\; the\, thing", "DTSTART:20260115T083000Z"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
