import unittest
from dataclasses import replace
from gateway import Refused
from release_preflight import Candidate,Evidence,REQUIRED,preflight

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.c=Candidate("a"*40,"b"*40,"c"*64)
    def record(self,category,actor,outcome="PASS"):
        return Evidence(category,self.c,actor,outcome,"fixture-"+category,"d"*64)
    def test_empty_is_incomplete(self):
        r=preflight(self.c,())
        self.assertEqual(len(r.missing),12)
        self.assertEqual(r.disposition,"INCOMPLETE")
    def test_ci_cannot_fill_owner_or_reviewer_gates(self):
        rows=tuple(self.record(c,a) for c,a in REQUIRED if a=="automation")
        r=preflight(self.c,rows)
        self.assertEqual(len(r.supplied_pass),6)
        self.assertIn("independent-review",r.missing)
        self.assertIn("clean-install-rollback",r.missing)
        with self.assertRaises(Refused):
            preflight(self.c,rows+(self.record("cosmic-picker","automation"),))
    def test_even_complete_supplied_records_require_real_review(self):
        rows=tuple(self.record(c,a) for c,a in REQUIRED)
        r=preflight(self.c,rows)
        self.assertEqual(r.disposition,"REVIEW_REQUIRED")
        self.assertEqual(len(r.supplied_pass),12)
        self.assertEqual(r,preflight(self.c,tuple(reversed(rows))))
    def test_exact_commit_tree_and_package_match(self):
        row=self.record("gtk","automation")
        for new in (replace(self.c,commit="e"*40),replace(self.c,tree="e"*40),replace(self.c,package="e"*64)):
            with self.assertRaises(Refused):
                preflight(new,(row,))
    def test_failure_and_hold_not_overwritten(self):
        row=self.record("gtk","automation","FAIL")
        r=preflight(self.c,(row,self.record("independent-review","reviewer","HOLD")))
        self.assertEqual(r.disposition,"BLOCKED")
        self.assertEqual(r.failed,("gtk",))
        self.assertEqual(r.held,("independent-review",))
        with self.assertRaises(Refused):
            preflight(self.c,(row,replace(row,outcome="PASS")))
    def test_digest_changes_with_evidence_reference(self):
        row=self.record("gtk","automation")
        self.assertNotEqual(preflight(self.c,(row,)).digest,
                            preflight(self.c,(replace(row,reference="another"),)).digest)
    def test_malformed_and_unknown_fields_refused(self):
        row=self.record("gtk","automation")
        for altered in (replace(row,category="unknown"),replace(row,outcome="SKIP"),replace(row,artifact_digest="fake")):
            with self.assertRaises(Refused):
                preflight(self.c,(altered,))
