# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Conflict-rehearsal tests: historical and disposable Git fixtures."""
import os
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from tools.conflict_check import local_branches, report, merge_tree


def setUpModule():
    # Historical checks require this checkout, not the caller's cwd. The
    # installed-package suite deliberately starts outside the source tree.
    unittest.addModuleCleanup(os.chdir, os.getcwd())
    os.chdir(Path(__file__).resolve().parents[1])


class ReportTests(unittest.TestCase):
    def entries(self, out=None, err=None):
        return StringIO(), StringIO()

    def test_a_branch_that_applies_cleanly_reports_no_problem(self):
        if "feat/usage-report" not in local_branches():
            self.skipTest("this clone does not carry the pair")
        out, err = self.entries()
        code = report([{"number": 1, "head": "feat/approved-sources",
                        "base": "feat/usage-report"}], out=out, err=err)
        self.assertEqual(code, 0)
        self.assertIn("applies cleanly", out.getvalue())
        self.assertIn("1 branches checked, 0 problems", out.getvalue())

    def test_a_missing_base_is_reported_rather_than_treated_as_clean(self):
        out, err = self.entries()
        code = report([{"number": 2, "head": "feat/approved-sources",
                        "base": "no-such-base"}], out=out, err=err)
        self.assertIn("0 problems", out.getvalue())
        self.assertIn("base missing locally", out.getvalue())

    def test_an_entry_without_a_head_or_base_is_a_problem(self):
        out, err = self.entries()
        code = report([{"number": 3, "head": "", "base": ""}], out=out, err=err)
        self.assertEqual(code, 1)
        self.assertIn("no head or base to rehearse", err.getvalue())


class RehearsalTests(unittest.TestCase):
    """The rehearsal against this repository's own history."""

    def report_for(self, plan):
        out, err = StringIO(), StringIO()
        code = report(plan, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def need(self, *branches):
        """Skip when a branch this rehearsal needs is not in this clone.

        A merge-tree rehearsal is a local tool: a CI checkout fetches one
        branch, so those clones cannot rehearse and say so rather than failing.
        """
        have = local_branches()
        if any(branch not in have for branch in branches):
            self.skipTest("this clone does not carry %s" % (", ".join(
                branch for branch in branches if branch not in have),))

    def test_the_top_of_the_stack_applies_cleanly(self):
        self.need("feat/approved-sources", "feat/usage-report")
        code, out, _ = self.report_for([{"number": 66, "head": "feat/approved-sources",
                                         "base": "feat/usage-report"}])
        self.assertEqual(code, 0)
        self.assertIn("feat/approved-sources            applies cleanly", out)

    def test_the_two_branches_that_conflicted_now_apply_cleanly(self):
        # PRs #9 and #12 were rebased after the rehearsal found them conflicting
        # on their bases; both apply cleanly now, which is the fix's evidence.
        self.need("feat/profile-ui", "feat/modes-and-profiles",
                  "feat/market-ui")
        _, out, _ = self.report_for([{"number": 9, "head": "feat/profile-ui",
                                   "base": "feat/modes-and-profiles"},
                                  {"number": 12, "head": "feat/market-ui",
                                   "base": "feat/profile-ui"}])
        self.assertIn("#9 feat/profile-ui                  applies cleanly", out)
        self.assertIn("#12 feat/market-ui                   applies cleanly", out)
        self.assertNotIn("CONFLICT", out)

    def test_a_conflicted_path_list_never_contains_a_message(self):
        # The parser reads only the path list: merge-tree's message section
        # starts after a blank line and is never reported as a path.
        self.need("feat/market-consolidated", "feat/market-ui")
        _, out, _ = self.report_for([{"number": 19, "head": "feat/market-consolidated",
                                   "base": "feat/market-ui"}])
        self.assertIn("merge it, do not rebase", out)
        self.assertNotIn("Auto-merging", out)
        self.assertNotIn("CONFLICT (content)", out)

    def test_a_missing_head_is_not_silently_clean(self):
        self.need("feat/usage-report")
        code, out, _ = self.report_for([{"number": 7, "head": "no-such-head",
                                         "base": "feat/usage-report"}])
        self.assertEqual(code, 0)
        self.assertIn("head missing locally", out)


class MergePRTests(unittest.TestCase):
    """A pull request that carries a forked line is merged, not rebased."""

    def report_for(self, plan):
        out, err = StringIO(), StringIO()
        report(plan, out=out, err=err)
        return out.getvalue(), err.getvalue()

    def need(self, *branches):
        have = local_branches()
        if any(branch not in have for branch in branches):
            self.skipTest("this clone does not carry %s"
                          % ", ".join(b for b in branches if b not in have))

    def test_a_merge_pr_is_reported_as_merge_not_rebase(self):
        self.need("feat/market-consolidated", "feat/market-ui")
        out, _ = self.report_for([{"number": 19, "head": "feat/market-consolidated",
                                   "base": "feat/market-ui"}])
        self.assertIn("merge it, do not rebase", out)

    def test_a_normal_branch_is_not_mistaken_for_a_merge(self):
        self.need("feat/approved-sources", "feat/usage-report")
        out, _ = self.report_for([{"number": 66, "head": "feat/approved-sources",
                                   "base": "feat/usage-report"}])
        self.assertIn("applies cleanly", out)
        self.assertNotIn("merge it, do not rebase", out)


class DisposableRehearsalTests(unittest.TestCase):
    """Always exercise actual Git merge-tree, even in a shallow CI checkout."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Synthetic reviewer")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.commit("shared.txt", "original\n", "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, filename, data, message):
        (self.root / filename).write_text(data, encoding="utf-8")
        self.git("add", filename)
        self.git("commit", "-m", message)

    def test_clean_divergent_branches_without_checkout_or_merge(self):
        self.git("switch", "-c", "feature")
        self.commit("feature.txt", "feature\n", "feature")
        self.git("switch", "main")
        self.commit("main.txt", "main\n", "main")
        before = self.git("rev-parse", "HEAD")
        self.assertEqual(merge_tree("main", "feature"), (True, []))
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.root / "feature.txt").exists())

    def test_conflict_reports_only_path_and_preserves_worktree(self):
        self.git("switch", "-c", "feature")
        self.commit("shared.txt", "feature\n", "feature")
        self.git("switch", "main")
        self.commit("shared.txt", "main\n", "main")
        before = self.git("rev-parse", "HEAD")
        self.assertEqual(merge_tree("main", "feature"), (False, ["shared.txt"]))
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual((self.root / "shared.txt").read_text(), "main\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
