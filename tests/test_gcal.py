import os
import stat
import tempfile
import threading
import unittest
import urllib.request

from workdesk.collectors.base import ConfigError, parse_query
from workdesk.collectors.gcal import (GoogleCalendar, FileTokenStore, MemoryTokenStore, parse_event, TOKEN_URL)
from workdesk.config import DEFAULTS
from workdesk.demo import FixtureHttp, load_fixture, routes
from tests.util import NOW

CFG = dict(DEFAULTS["calendar"], enabled=True)
ENV = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"}


def cal(http=None, store=None):
    return GoogleCalendar(CFG, http or FixtureHttp(), ENV, store or MemoryTokenStore({"refresh_token": "rt"}))


class GcalTests(unittest.TestCase):
    def test_parse_event(self):
        evs = {e["id"]: parse_event(e) for e in load_fixture("gcal_events.json")["items"]}
        self.assertEqual(evs["e1"]["start"], "2026-01-15T09:00:00Z")
        self.assertTrue(evs["e3"]["declined"])
        self.assertTrue(evs["e4"]["all_day"])
        self.assertTrue(evs["e5"]["is_workdesk"])
        self.assertTrue(evs["e6"]["free"])
        self.assertTrue(evs["e7"]["cancelled"])

    def test_busy_excludes_everything_but_real_meetings(self):
        http = FixtureHttp()
        busy = cal(http).busy("2026-01-15", "Europe/Berlin")
        self.assertEqual([b["title"] for b in busy], ["Team standup", "Design review"])
        refresh = http.calls[0]
        self.assertEqual(refresh["url"], TOKEN_URL)
        self.assertEqual(refresh["form"]["grant_type"], "refresh_token")
        ev = http.calls[1]
        self.assertEqual((ev["params"]["singleEvents"], ev["params"]["orderBy"]), ("true", "startTime"))
        self.assertEqual(ev["params"]["timeMin"], "2026-01-14T23:00:00Z")
        self.assertEqual(ev["headers"]["Authorization"], "Bearer demo-access")

    def test_busy_ignores_other_days(self):
        self.assertEqual(cal().busy("2026-01-16", "Europe/Berlin"), [])

    def test_insert_block_body(self):
        http = FixtureHttp()
        eid = cal(http).insert_block("Write report", "2026-01-15T09:00:00Z", "2026-01-15T10:00:00Z", "https://x/y")
        self.assertEqual(eid, "demo-event-1")
        body = http.calls[-1]["json"]
        self.assertEqual(body["summary"], "Focus: Write report")
        self.assertEqual((body["visibility"], body["transparency"]), ("private", "opaque"))
        self.assertEqual(body["description"], "https://x/y")
        self.assertEqual(body["extendedProperties"]["private"], {"workdesk": "1"})

    def test_not_authorised_and_missing_env(self):
        with self.assertRaisesRegex(ConfigError, "workdesk auth google"):
            cal(store=MemoryTokenStore()).busy("2026-01-15", "UTC")
        with self.assertRaisesRegex(ConfigError, "missing env var GOOGLE_CLIENT_ID"):
            GoogleCalendar(CFG, FixtureHttp(), {}, MemoryTokenStore({"refresh_token": "r"})).busy("2026-01-15", "UTC")

    def test_token_file_mode_600(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "google_token.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as f:
                f.write("{}")
            os.chmod(p, 0o644)
            st = FileTokenStore(p)
            st.save({"refresh_token": "abc"})
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(st.load(), {"refresh_token": "abc"})
            self.assertIsNone(FileTokenStore(os.path.join(d, "none.json")).load())

    def authorize(self, state_override=None):
        http = FixtureHttp([(r"oauth2\.googleapis\.com/token", {"refresh_token": "rt-new", "scope": "s"})])
        store = MemoryTokenStore()
        seen = {}

        def browser(url):
            q = parse_query(url)
            seen.update(q)
            redirect = q["redirect_uri"] + "/?code=abc&state=" + (state_override or q["state"])
            threading.Thread(target=lambda: urllib.request.urlopen(redirect, timeout=5).read()).start()

        c = cal(http, store)
        try:
            c.authorize(open_url=browser, timeout=10, say=lambda *a: None)
        finally:
            self.seen, self.http, self.store = seen, http, store

    def test_authorize_flow(self):
        self.authorize()
        self.assertEqual(self.store.load()["refresh_token"], "rt-new")
        q = self.seen
        self.assertEqual((q["code_challenge_method"], q["access_type"], q["prompt"], q["response_type"]),
                         ("S256", "offline", "consent", "code"))
        self.assertEqual(q["scope"], "https://www.googleapis.com/auth/calendar.events")
        self.assertTrue(q["redirect_uri"].startswith("http://127.0.0.1:"))
        form = self.http.calls[0]["form"]
        self.assertEqual((form["code"], form["grant_type"]), ("abc", "authorization_code"))
        self.assertTrue(len(form["code_verifier"]) >= 43)

    def test_authorize_state_mismatch(self):
        with self.assertRaisesRegex(ConfigError, "state mismatch"):
            self.authorize("wrong")
        self.assertIsNone(self.store.load())


if __name__ == "__main__":
    unittest.main()
