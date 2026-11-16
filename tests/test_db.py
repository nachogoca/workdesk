import unittest
from datetime import timedelta

from workdesk.models import iso
from tests.util import NOW, mem_db, item, ago


class UpsertTests(unittest.TestCase):
    def setUp(self):
        self.db = mem_db()

    def test_overrides_survive_upsert(self):
        self.db.upsert_items([item(title="old")], NOW)
        self.db.set_override("jira:MLP-1", "importance", 85)
        self.db.set_override("jira:MLP-1", "urgency", 25)
        self.db.set_estimate("jira:MLP-1", 90)
        self.db.upsert_items([item(title="new", labels=["x"])], NOW + timedelta(hours=1))
        r = self.db.get_item("jira:MLP-1")
        self.assertEqual((r["title"], r["labels"]), ("new", ["x"]))
        self.assertEqual((r["importance_override"], r["urgency_override"], r["estimate_min"]), (85, 25, 90))
        self.assertEqual(r["first_seen"], iso(NOW))

    def test_close_missing(self):
        self.db.upsert_items([item("jira:A"), item("jira:B"), item("github:x/y#1", source="github")], NOW)
        n = self.db.close_missing("jira", ["jira:A"], NOW)
        self.assertEqual(n, 1)
        self.assertEqual(self.db.get_item("jira:B")["state"], "closed")
        self.assertEqual(self.db.get_item("jira:A")["state"], "open")
        self.assertEqual(self.db.get_item("github:x/y#1")["state"], "open")

    def test_closed_item_reopens(self):
        self.db.upsert_items([item("jira:A")], NOW)
        self.db.close_missing("jira", [], NOW)
        self.db.upsert_items([item("jira:A")], NOW)
        self.assertEqual(self.db.get_item("jira:A")["state"], "open")

    def test_done_reopens_only_on_newer_activity(self):
        self.db.upsert_items([item("jira:A", updated_at=ago(5))], NOW)
        self.db.set_state("jira:A", "done", NOW)
        self.db.upsert_items([item("jira:A", updated_at=ago(5))], NOW)
        self.assertEqual(self.db.get_item("jira:A")["state"], "done")
        self.db.upsert_items([item("jira:A", updated_at=iso(NOW + timedelta(minutes=5)))], NOW)
        self.assertEqual(self.db.get_item("jira:A")["state"], "open")

    def test_manual_never_reopens(self):
        mid = self.db.add_manual("task", NOW)
        self.assertRegex(mid, r"^manual:[0-9a-f]{8}$")
        self.db.set_state(mid, "done", NOW)
        from workdesk.models import Item
        self.db.upsert_items([Item(id=mid, source="manual", kind="do", title="task",
                                   updated_at=iso(NOW + timedelta(days=1)))], NOW)
        self.assertEqual(self.db.get_item(mid)["state"], "done")

    def test_manual_importance_is_override(self):
        mid = self.db.add_manual("task", NOW, importance=85)
        self.assertEqual(self.db.get_item(mid)["importance_override"], 85)

    def test_snooze_expiry(self):
        self.db.upsert_items([item("jira:A")], NOW)
        self.db.set_state("jira:A", "snoozed", NOW, snooze_until=iso(NOW + timedelta(days=1)))
        self.assertEqual(self.db.list_items(NOW), [])
        self.assertEqual(len(self.db.list_items(NOW + timedelta(hours=23))), 0)
        self.assertEqual(len(self.db.list_items(NOW + timedelta(days=1))), 1)

    def test_aliases_stable_and_resolve(self):
        self.db.upsert_items([item("jira:A"), item("jira:B")], NOW)
        a = self.db.alias_for("jira:A", "2026-01-15")
        b = self.db.alias_for("jira:B", "2026-01-15")
        self.assertEqual((a, b), ("j1", "j2"))
        self.assertEqual(self.db.alias_for("jira:A", "2026-01-15"), "j1")
        self.assertEqual(self.db.resolve("j2", "2026-01-15"), "jira:B")
        self.assertEqual(self.db.resolve("jira:B", "2026-01-15"), "jira:B")
        self.db.alias_for("jira:A", "2026-01-16")
        self.assertEqual(self.db.rows("SELECT * FROM aliases WHERE day='2026-01-15'"), [])


if __name__ == "__main__":
    unittest.main()
