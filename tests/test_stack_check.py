# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Stack-check tests: no repository is contacted."""
import unittest
from io import StringIO

from tools.stack_check import chain, report


def pr(number, head, base, draft=True, state="SUCCESS", open_state="OPEN"):
    return {"number": number, "headRefName": head, "baseRefName": base,
            "isDraft": draft, "state": open_state, "conclusion": state}


GOOD = [pr(1, "feat/core", "main"),
        pr(2, "feat/ui", "feat/core"),
        pr(3, "feat/last", "feat/ui")]


class ChainTests(unittest.TestCase):
    def test_a_well_formed_stack_is_mergeable_in_order(self):
        order, problems = chain(GOOD)
        self.assertEqual([row["number"] for row in order], [1, 2, 3])
        self.assertEqual(problems, [])

    def test_a_branch_based_on_the_wrong_parent_is_reported(self):
        wrong = [pr(1, "feat/core", "main"), pr(2, "feat/ui", "main")]
        order, problems = chain(wrong)
        self.assertEqual([row["number"] for row in order], [1, 2])
        self.assertEqual(len(problems), 1)
        self.assertIn("#2 (feat/ui) is based on main, expected feat/core", problems[0])

    def test_a_pull_request_that_touches_main_is_reported(self):
        _, problems = chain([pr(1, "main", "main")])
        self.assertEqual(len(problems), 1)
        self.assertIn("targets the main branch", problems[0])

    def test_a_missing_run_or_a_ready_pr_is_reported(self):
        red = [pr(1, "feat/core", "main", state="FAILURE"),
               pr(2, "feat/ui", "feat/core", draft=False)]
        _, problems = chain(red)
        self.assertEqual(len(problems), 2)
        self.assertIn("no green run (FAILURE)", problems[0])
        self.assertIn("is not a draft", problems[1])

    def test_a_missing_branch_is_reported_not_guessed(self):
        _, problems = chain(GOOD, order=["feat/core", "feat/missing"])
        self.assertEqual(problems, ["no open pull request for feat/missing"])

    def test_the_report_states_the_evidence(self):
        out, err = StringIO(), StringIO()
        code = report(GOOD, out=out)
        self.assertEqual(code, 0)
        self.assertIn("3 pull requests, 3 mergeable in order, 0 problems", out.getvalue())
        line = out.getvalue().splitlines()[1]
        self.assertIn("#1", line)
        self.assertIn("feat/core", line)
        self.assertIn("main", line)
        code = report([pr(1, "feat/core", "main", state="FAILURE")], out=out, err=err)
        self.assertEqual(code, 1)
        self.assertIn("no green run (FAILURE)", err.getvalue())
