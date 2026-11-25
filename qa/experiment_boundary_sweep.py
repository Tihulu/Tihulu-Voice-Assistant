import unittest
from unittest.mock import patch
from dataclasses import replace
from hashlib import sha256
from gateway import Context,Request,FixtureGateway,Refused
from tihulu_jarvis import profiles
from test_integrated_workflow import IntegratedTests
from test_transfer_resume import ResumeTests
from transfer_resume import resume

class WorkflowSweep(IntegratedTests):
    def test_close_during_compute_prevents_publication(self):
        from tihulu_jarvis.temperature_engine import analyze_temperature as actual
        def closing(*args,**kwargs):
            result=actual(*args,**kwargs)
            self.w.close()
            return result
        with patch("integrated_workflow.analyze_temperature",side_effect=closing), patch("integrated_workflow.envelope") as disclose:
            with self.assertRaises(Refused):
                self.run_fixture()
            disclose.assert_not_called()
        self.assertEqual(self.w.state,"closed")
        self.assertEqual(self.i.removal_plan("jan"),"REFERENCE_ONLY")

class ResumeSweep(ResumeTests):
    def test_forged_old_grant_cannot_reuse_previous_authority(self):
        forged=replace(self.receipt,old_grant="fabricated")
        with self.assertRaises(Refused):
            resume(self.g,self.old,self.ctx,self.m,forged,self.prefix)

    def test_mutated_receipt_prefix_cannot_resume(self):
        forged=replace(self.receipt,prefix_digest=sha256(b"x"*5).hexdigest())
        with self.assertRaises(Refused):
            resume(self.g,self.new,self.ctx,self.m,forged,b"x"*5)

if __name__=="__main__":
    suite=unittest.TestSuite()
    suite.addTest(WorkflowSweep("test_close_during_compute_prevents_publication"))
    suite.addTest(ResumeSweep("test_forged_old_grant_cannot_reuse_previous_authority"))
    suite.addTest(ResumeSweep("test_mutated_receipt_prefix_cannot_resume"))
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
