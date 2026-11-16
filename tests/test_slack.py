import unittest

from workdesk.collectors.slack import SlackCollector, SlackError, is_answered, parse_match
from workdesk.config import DEFAULTS
from workdesk.demo import FixtureHttp, load_fixture
from tests.util import NOW

CFG = dict(DEFAULTS["slack"], enabled=True)
ENV = {"SLACK_TOKEN": "t"}


def collect(**over):
    http = FixtureHttp()
    return {i.id: i for i in SlackCollector(dict(CFG, **over), http, ENV).collect(NOW)}, http


class SlackTests(unittest.TestCase):
    def test_default_items(self):
        items, _ = collect()
        self.assertEqual(sorted(items), ["slack:C_ENG:1768467600.000100", "slack:C_PRIV:1768456800.000700",
                                         "slack:D_DANA:1768472000.000500"])
        eng = items["slack:C_ENG:1768467600.000100"]
        self.assertEqual(eng.kind, "respond")
        self.assertTrue(eng.title.startswith("#eng: "))
        self.assertEqual(eng.updated_at, "2026-01-15T10:00:00Z")  # newest unanswered mention
        self.assertEqual(eng.created_at, "2026-01-15T09:00:00Z")
        self.assertFalse(eng.meta["is_dm"])
        dm = items["slack:D_DANA:1768472000.000500"]
        self.assertTrue(dm.meta["is_dm"])
        self.assertTrue(dm.title.startswith("@dana: "))

    def test_answered_thread_skipped_and_failed_check_kept(self):
        items, _ = collect()
        self.assertNotIn("slack:C_OPS:1768460400.000300", items)  # I replied later
        self.assertIn("slack:C_PRIV:1768456800.000700", items)  # replies call returned missing_scope

    def test_reaction_counts_as_done(self):
        self.assertNotIn("slack:D_BOB:1768474800.000600", collect()[0])
        items, _ = collect(reaction_counts_as_done=False)
        self.assertEqual(len(items), 4)
        self.assertIn("slack:D_BOB:1768474800.000600", items)

    def test_own_messages_old_and_duplicates_skipped(self):
        items, http = collect(reaction_counts_as_done=False)
        self.assertFalse([i for i in items if "1768470000" in i or "1767700800" in i])
        self.assertEqual(len([c for c in http.calls if "search.messages" in c["url"]]), 2)

    def test_permalink_thread_ts(self):
        m = parse_match(load_fixture("slack_search_mention.json")["messages"]["matches"][3])
        self.assertEqual(m["thread_ts"], "1768450000.000800")

    def test_is_answered(self):
        msgs = [{"ts": "10.000001", "user": "U1"}, {"ts": "11.000000", "user": "ME"}]
        self.assertTrue(is_answered(msgs, "10.000001", "ME", False))
        self.assertFalse(is_answered(msgs, "11.000000", "ME", False))
        msgs = [{"ts": "10.000001", "user": "U1", "reactions": [{"users": ["ME"]}]}]
        self.assertTrue(is_answered(msgs, "10.000001", "ME", True))
        self.assertFalse(is_answered(msgs, "10.000001", "ME", False))

    def test_slack_error_message(self):
        http = FixtureHttp([(r"auth.test", {"ok": False, "error": "invalid_auth"})])
        with self.assertRaisesRegex(SlackError, "slack auth.test: invalid_auth"):
            SlackCollector(CFG, http, ENV).collect(NOW)


if __name__ == "__main__":
    unittest.main()
