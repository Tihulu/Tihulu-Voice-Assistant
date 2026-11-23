# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""CI-trigger tests: the workflow text is the fixture, nothing is fetched."""
import unittest
from unittest.mock import patch

from tools.ci_triggers import current, rewrite

SAMPLE = """name: Core and native GTK tests
on:
  pull_request:
    branches: [main, 'feat/core', 'feat/ui',
               'feat/market']
  workflow_dispatch: {}
jobs:
  test:
    runs-on: ubuntu-24.04
"""


class ParseTests(unittest.TestCase):
    def test_the_list_is_read_in_order_from_a_wrapped_block(self):
        self.assertEqual(current(SAMPLE), ("main", "feat/core", "feat/ui", "feat/market"))

    def test_a_workflow_without_a_list_says_so(self):
        with self.assertRaises(SystemExit):
            current("name: nothing here\\n")

    def test_other_lists_are_not_mistaken_for_the_trigger_list(self):
        text = SAMPLE.replace("branches: [main, 'feat/core'",
                              "some-branches: [main, 'feat/core'")
        self.assertIn("branches: [main, 'feat/core'", SAMPLE)
        with self.assertRaises(SystemExit):
            current(text)


class RewriteTests(unittest.TestCase):
    def test_rewriting_keeps_the_shape_and_adds_the_missing_names(self):
        out = rewrite(SAMPLE, ["feat/core", "feat/ui", "feat/market", "feat/new"])
        self.assertEqual(current(out), ("main", "feat/core", "feat/ui",
                                        "feat/market", "feat/new"))
        self.assertIn("jobs:", out)
        self.assertIn("workflow_dispatch", out)

    def test_a_rewritten_workflow_parses_as_a_real_yaml_list(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        out = rewrite(SAMPLE, ["feat/core", "feat/ui", "feat/market", "feat/new"])
        data = yaml.safe_load(out)
        branches = data[True]["pull_request"]["branches"]
        self.assertEqual(branches, ["main", "feat/core", "feat/ui",
                                    "feat/market", "feat/new"])
        self.assertIn("test", data["jobs"])

    def test_a_stale_name_is_dropped_only_when_asked(self):
        out = rewrite(SAMPLE, ["feat/core"])
        self.assertEqual(current(out), ("main", "feat/core"))

    def test_the_rewritten_text_is_idempotent(self):
        once = rewrite(SAMPLE, ["feat/core", "feat/ui", "feat/market", "feat/new"])
        self.assertEqual(rewrite(once, ["feat/core", "feat/ui", "feat/market",
                                        "feat/new"]), once)


class CheckTests(unittest.TestCase):
    def test_missing_and_stale_are_separate_facts(self):
        import tempfile
        from pathlib import Path
        from tools import ci_triggers
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ci.yml"
            path.write_text(SAMPLE, encoding="utf-8")
            with patch.object(ci_triggers, "remote_branches",
                              return_value=("feat/core", "feat/ui", "feat/extra")):
                missing, stale = ci_triggers.check(path)
        self.assertEqual(missing, ("feat/extra",))
        self.assertEqual(stale, ("feat/market",))
