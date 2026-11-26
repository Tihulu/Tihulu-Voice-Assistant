# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from engine_isolation_acceptance import PREFLIGHT, preflight_passed

class IsolationPreflightTests(unittest.TestCase):
    def rows(self):
        return [{"case":name,"status":"PASS"} for name in PREFLIGHT]

    def test_exact_complete_preflight(self):
        self.assertTrue(preflight_passed(self.rows(),PREFLIGHT))

    def test_each_failure_and_hold_denies(self):
        for index in range(len(PREFLIGHT)):
            for status in ("FAIL","HOLD","UNKNOWN"):
                rows=self.rows()
                rows[index]["status"]=status
                self.assertFalse(preflight_passed(rows,PREFLIGHT))

    def test_missing_duplicate_and_reordered_denied(self):
        rows=self.rows()
        for changed in (rows[:-1],rows+[rows[-1]],rows[::-1],rows[:-1]+[rows[0]]):
            self.assertFalse(preflight_passed(changed,PREFLIGHT))

    def test_malformed_rows_denied(self):
        for value in (None,"PASS",{},{"status":"PASS"}, {"case":PREFLIGHT[-1]}):
            self.assertFalse(preflight_passed(self.rows()[:-1]+[value],PREFLIGHT))
