import contextlib
import io
import os
import tempfile
import unittest

from workdesk.cli import main
from tests.util import NOW


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"WORKDESK_DB": os.path.join(self.tmp.name, "w.db"),
                    "WORKDESK_CONFIG": os.path.join(self.tmp.name, "none.toml")}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv), env=self.env, now=NOW)
        return code, out.getvalue(), err.getvalue()

    def test_add_list_set_snooze_done(self):
        code, out, _ = self.run_cli("add", "Write report", "--due", "2026-01-15", "--importance", "high")
        self.assertEqual(code, 0)
        self.assertIn("m1", out)
        _, out, _ = self.run_cli("list")
        self.assertIn("Write report", out)
        self.assertIn("m1", out)
        self.assertEqual(self.run_cli("set", "m1", "urgency", "25")[0], 0)
        self.assertIn("U25", self.run_cli("list")[1])
        self.assertEqual(self.run_cli("set", "m1", "estimate", "30")[0], 0)
        self.assertEqual(self.run_cli("snooze", "m1", "1w")[0], 0)
        self.assertIn("no open items", self.run_cli("list")[1])
        self.assertEqual(self.run_cli("list", "--state", "snoozed")[0], 0)
        self.assertEqual(self.run_cli("done", "m1")[0], 0)
        self.assertIn("Write report", self.run_cli("list", "--state", "done")[1])

    def test_demo_plan_accept_ics(self):
        self.env["WORKDESK_DEMO_DB"] = os.path.join(self.tmp.name, "demo.db")
        ics = os.path.join(self.tmp.name, "out.ics")
        self.assertEqual(self.run_cli("demo", "--no-serve")[0], 0)
        code, out, err = self.run_cli("--demo", "plan", "--accept", "all", "--ics", ics)
        self.assertEqual(code, 0, err)
        self.assertIn("2 meetings", out)
        self.assertIn("(accepted)", out)
        self.assertNotIn("(proposed)", out)
        with open(ics, newline="") as f:
            text = f.read()
        self.assertIn("BEGIN:VEVENT", text)
        self.assertIn("\r\n", text)
        self.assertEqual(self.run_cli("--demo", "plan", "--accept", "999")[0], 1)

    def doctor(self, http, env, **cfgover):
        path = os.path.join(self.tmp.name, "cfg.toml")
        with open(path, "w") as f:
            f.write("[jira]\nenabled = true\n[github]\nenabled = true\n[slack]\nenabled = true\n"
                    f"[calendar]\nenabled = true\ntoken_file = '{self.tmp.name}/tok.json'\n")
        self.env.update(WORKDESK_CONFIG=path, **env)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["doctor"], env=self.env, http=http, now=NOW)
        return code, out.getvalue()

    def test_doctor_reasons(self):
        from workdesk.collectors.base import HttpError
        from workdesk.demo import FixtureHttp, routes
        toks = {"JIRA_TOKEN": "x", "GITHUB_TOKEN": "x", "SLACK_TOKEN": "x", "GOOGLE_CLIENT_ID": "a",
                "GOOGLE_CLIENT_SECRET": "b"}
        code, out = self.doctor(FixtureHttp(), {k: v for k, v in toks.items() if k != "JIRA_TOKEN"})
        self.assertEqual(code, 1)
        self.assertIn("FAIL jira: missing env var JIRA_TOKEN", out)
        self.assertIn("pass github: authenticated as me-example", out)
        self.assertIn("pass slack: authenticated as me", out)
        self.assertIn("FAIL calendar: not authorised", out)
        table = [(r"/user$", HttpError(401, "https://api.github.com/user?x=secret")),
                 (r"auth\.test", {"ok": False, "error": "missing_scope", "needed": "search:read"})] + routes()
        code, out = self.doctor(FixtureHttp(table), toks)
        self.assertIn("pass jira: authenticated as Me Example", out)
        self.assertIn("FAIL github: 401 unauthorised (bad or expired token)", out)
        self.assertIn("FAIL slack: missing scope", out)
        self.assertNotIn("secret", out)

    def test_doctor_skips_disabled(self):
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("skip "), 4)

    def test_errors(self):
        code, _, err = self.run_cli("done", "zz9")
        self.assertEqual(code, 1)
        self.assertIn("unknown item", err)
        self.assertEqual(self.run_cli("add", "x", "--due", "bad")[0], 1)

    def test_init_writes_config(self):
        self.env["WORKDESK_CONFIG"] = os.path.join(self.tmp.name, "sub", "config.toml")
        self.assertEqual(self.run_cli("init")[0], 0)
        self.assertTrue(os.path.exists(self.env["WORKDESK_CONFIG"]))


if __name__ == "__main__":
    unittest.main()
