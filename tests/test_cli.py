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
