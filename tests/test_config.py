import os
import tempfile
import unittest

from workdesk import config
from workdesk.config import EXAMPLE, DEFAULTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigTests(unittest.TestCase):
    def test_example_file_identical(self):
        with open(os.path.join(ROOT, "config.example.toml")) as f:
            self.assertEqual(f.read(), EXAMPLE)

    def test_sources_disabled_by_default(self):
        for s in ("jira", "github", "slack", "calendar"):
            self.assertFalse(DEFAULTS[s]["enabled"])

    def test_no_secret_values_only_names(self):
        for s in ("jira", "github", "slack"):
            self.assertTrue(DEFAULTS[s]["token_env"].isupper())

    def test_merge_and_env_paths(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.toml")
            with open(p, "w") as f:
                f.write('[general]\nport = 9000\n[jira]\nenabled = true\n')
            env = {"WORKDESK_CONFIG": p}
            cfg = config.load_config(env)
            self.assertEqual(cfg["general"]["port"], 9000)
            self.assertEqual(cfg["general"]["timezone"], "Europe/Berlin")
            self.assertTrue(cfg["jira"]["enabled"])
            self.assertEqual(cfg["jira"]["auth"], "basic")
            self.assertEqual(config.db_path(cfg, {"WORKDESK_DB": "/x/y.db"}), "/x/y.db")
            self.assertTrue(config.db_path(cfg, {}).endswith("/.local/share/workdesk/workdesk.db"))


if __name__ == "__main__":
    unittest.main()
