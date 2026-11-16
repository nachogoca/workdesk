import unittest

from workdesk import scoring
from tests.util import NOW, cfg, ago, sitem

C = cfg()


def U(**kw):
    return scoring.compute_urgency(sitem(**kw), NOW, C)


def I(**kw):
    return scoring.compute_importance(sitem(**kw), C)


class UrgencyTests(unittest.TestCase):
    def test_due_rules(self):
        cases = {"2026-01-14": 100, "2026-01-15": 90, "2026-01-16": 80, "2026-01-18": 65,
                 "2026-01-22": 45, "2026-02-20": 20}
        for due, want in cases.items():
            self.assertEqual(U(due=due)[0], want, due)

    def test_due_uses_timezone(self):
        late = NOW.replace(hour=23, minute=30)  # already Jan 16 in Berlin
        self.assertEqual(scoring.compute_urgency(sitem(due="2026-01-16"), late, C)[0], 90)

    def test_hot_label(self):
        self.assertEqual(U(labels=["Incident"])[0], 95)
        self.assertEqual(U(labels=["nothing"])[0], 15)

    def test_respond_review_by_age(self):
        for h, want in ((80, 90), (50, 80), (30, 70), (10, 55), (3, 40), (1, 30)):
            self.assertEqual(U(kind="respond", updated_at=ago(h))[0], want, h)
            self.assertEqual(U(kind="review", updated_at=ago(h))[0], want, h)

    def test_do_without_due(self):
        self.assertEqual(U(meta={"in_sprint": True, "in_progress": True})[0], 45)
        self.assertEqual(U(meta={"in_sprint": True})[0], 35)
        self.assertEqual(U(meta={})[0], 15)

    def test_do_with_due_uses_due_only(self):
        self.assertEqual(U(due="2026-02-20", meta={"in_sprint": True, "in_progress": True})[0], 20)

    def test_waiting_and_nudge(self):
        self.assertEqual(U(kind="waiting", updated_at=ago(days=2))[0], 10)
        s, reason = U(kind="waiting", updated_at=ago(days=5))
        self.assertEqual(s, 50)
        self.assertIn("nudge", reason)

    def test_highest_wins(self):
        self.assertEqual(U(kind="respond", updated_at=ago(80), due="2026-01-15")[0], 90)
        self.assertEqual(U(kind="respond", updated_at=ago(80), labels=["sev1"])[0], 95)


class ImportanceTests(unittest.TestCase):
    def test_jira_priority(self):
        for p, want in (("Highest", 90), ("High", 75), ("Medium", 55), ("Low", 35), ("Lowest", 20), (None, 50)):
            self.assertEqual(I(meta={"priority": p})[0], want, p)

    def test_sprint_bonus_and_waiting_cap(self):
        self.assertEqual(I(meta={"priority": "Medium", "in_sprint": True})[0], 65)
        self.assertEqual(I(kind="waiting", meta={"priority": "Highest"})[0], 45)
        self.assertEqual(I(kind="waiting", meta={"priority": "Highest"}, labels=["sev1"])[0], 70)

    def test_other_sources(self):
        self.assertEqual(I(source="github", kind="review")[0], 60)
        self.assertEqual(I(source="github", kind="respond")[0], 55)
        self.assertEqual(I(source="slack", kind="respond")[0], 50)
        self.assertEqual(I(source="slack", kind="respond", meta={"is_dm": True})[0], 60)
        self.assertEqual(I(source="github", kind="waiting")[0], 35)
        self.assertEqual(I(source="manual")[0], 50)
        self.assertEqual(I(source="github", kind="do")[0], 50)

    def test_bonuses_and_clamp(self):
        c = cfg(general__vip_requesters=["Alice Example"])
        score = scoring.compute_importance(sitem(source="github", kind="review", requester="alice example"), c)[0]
        self.assertEqual(score, 80)
        self.assertEqual(I(meta={"priority": "Highest", "in_sprint": True}, labels=["sev1"])[0], 100)


class RankTests(unittest.TestCase):
    def test_overrides_win(self):
        s = scoring.score_item(sitem(urgency_override=10, importance_override=100, labels=["sev1"]), NOW, C)
        self.assertEqual((s["urgency"], s["importance"]), (10, 100))
        self.assertEqual(s["urgency_reason"], "override")
        self.assertEqual(s["rank"], 46.0)

    def test_quadrants(self):
        q = scoring.quadrant
        self.assertEqual(q(60, 60), "do_now")
        self.assertEqual(q(59, 60), "schedule")
        self.assertEqual(q(60, 59), "quick")
        self.assertEqual(q(10, 10), "later")

    def test_rank_formula_and_sort(self):
        a = sitem(id="a", urgency_override=100, importance_override=100)
        b = sitem(id="b", urgency_override=0, importance_override=0)
        r = scoring.rank_items([b, a], NOW, C)
        self.assertEqual([x["id"] for x in r], ["a", "b"])
        self.assertEqual(r[0]["rank"], 100.0)
        s = scoring.score_item(sitem(urgency_override=50, importance_override=50), NOW, C)
        self.assertEqual(s["rank"], 50.0)


if __name__ == "__main__":
    unittest.main()
