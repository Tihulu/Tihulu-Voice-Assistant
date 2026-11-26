# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from gateway import Refused
from local_agent_wave_run import WaveRun
from local_agent_resources import ResourceLedger
import test_local_agent_wave_run as fixtures

class AgentAttemptTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.WaveRunTests()
        f.setUp()
        self.ctx,self.models,self.plan,self.workers,self.g=f.ctx,f.models,f.plan,f.workers,f.g
        self.ledger=ResourceLedger(4096)
        self.a=self.new("first")
        self.b=self.new("second")
        self.ga=self.g.fixture_grant("first-plan",self.a.request("plan"),60)
        self.gb=self.g.fixture_grant("second-plan",self.b.request("plan"),60)

    def new(self,attempt):
        return WaveRun(self.g,self.plan,self.workers,self.models,4096,2,
                       ledger=self.ledger,run_id=attempt,attempt=attempt)

    def test_same_plan_different_attempts_have_distinct_requests(self):
        self.assertNotEqual(self.a.request("plan"),self.b.request("plan"))
        with self.assertRaises(Refused):
            self.b.start("plan",self.ga,self.ctx,self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        self.a.start("plan",self.ga,self.ctx,self.models)

    def test_old_completion_cannot_complete_new_attempt(self):
        self.a.start("plan",self.ga,self.ctx,self.models)
        self.a.cancel()
        self.b.start("plan",self.gb,self.ctx,self.models)
        for grant in (None,self.ga):
            with self.assertRaises(Refused):
                self.b.complete("plan",self.ctx,self.models,payload=b"late",completion_grant=grant)
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        self.b.complete("plan",self.ctx,self.models,payload=b"new",completion_grant=self.gb)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_late_failure_cannot_cancel_new_attempt(self):
        self.b.start("plan",self.gb,self.ctx,self.models)
        with self.assertRaises(Refused):
            self.b.fail("plan",self.ctx,self.models,completion_grant=self.ga)
        self.b.fail("plan",self.ctx,self.models,completion_grant=self.gb)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_invalid_worker_values_fail_closed(self):
        for worker in ([],{},None,True):
            for fn in (lambda:self.b.request(worker),
                       lambda:self.b.start(worker,self.gb,self.ctx,self.models),
                       lambda:self.b.complete(worker,self.ctx,self.models),
                       lambda:self.b.fail(worker,self.ctx,self.models)):
                with self.assertRaises(Refused):
                    fn()

    def test_invalid_attempt_identity_is_refused(self):
        for attempt in ("",[],True,"bad space"):
            with self.assertRaises(Refused):
                self.new(attempt)
