import unittest

from workdesk.demo import FixtureHttp, shift_value, FIXTURE_NOW
from tests.util import NOW


class DemoTests(unittest.TestCase):
    def test_fixture_now_matches(self):
        self.assertEqual(int(FIXTURE_NOW.timestamp()), 1768478400)
        self.assertEqual(FIXTURE_NOW, NOW)

    def test_shift(self):
        v = shift_value({"a": "2026-01-15", "b": "2026-01-15T10:00:00.000+0100", "c": "1768478400.000100",
                         "d": "plain", "e": 5, "f": ["https://x/p?thread_ts=1768478400.000100"]}, 2)
        self.assertEqual(v["a"], "2026-01-17")
        self.assertEqual(v["b"], "2026-01-17T10:00:00.000+0100")
        self.assertEqual(v["c"], "1768651200.000100")
        self.assertEqual((v["d"], v["e"]), ("plain", 5))
        self.assertIn("1768651200.000100", v["f"][0])

    def test_unshifted_request_key_and_recording(self):
        http = FixtureHttp([(r"ts=1768478400\.000100", {"ok": 1})], shift_days=2)
        self.assertEqual(http.request("GET", "https://s/x", params={"ts": "1768651200.000100"}), {"ok": 1})
        self.assertEqual(http.calls[0]["url"], "https://s/x")

    def test_unknown_route_fails_loudly(self):
        with self.assertRaises(AssertionError):
            FixtureHttp().request("GET", "https://nowhere.example/x")


if __name__ == "__main__":
    unittest.main()
