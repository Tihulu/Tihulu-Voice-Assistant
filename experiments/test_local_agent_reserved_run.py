# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import FixtureGateway,Refused
from tihulu_jarvis import profiles
from local_agent_wave_run import WaveRun
from local_agent_resources import ResourceLedger
import test_local_agent_wave_run as base

class ReservedRunTests(unittest.TestCase):
    def setUp(self):
        f=base.WaveRunTests()
        f.setUp()
        self.ctx,self.models,self.workers,self.plan=f.ctx,f.models,f.workers,f.plan
        self.g=f.g
        self.ledger=ResourceLedger(4096)
        self.run=WaveRun(self.g,self.plan,self.workers,self.models,4096,2,ledger=self.ledger,run_id="one")
        self.grants=f.grants

    def start(self,w):
        self.run.start(w,self.grants[w],self.ctx,self.models)

    def finish(self,w):
        self.run.complete(w,self.ctx,self.models)

    def test_complete_releases_only_own_reservation(self):
        other=self.ledger.reserve("other","worker",1024)
        self.start("plan")
        self.assertEqual(self.ledger.snapshot(),(4096,3072,1024))
        self.finish("plan")
        self.assertEqual(self.ledger.snapshot(),(4096,1024,3072))
        self.ledger.release(other)

    def test_denied_grant_rolls_back_reservation(self):
        with self.assertRaises(Refused):
            self.run.start("plan",self.grants["a"],self.ctx,self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        self.start("plan")
        self.finish("plan")

    def test_capacity_pressure_does_not_consume_grant(self):
        other=self.ledger.reserve("other","worker",3072)
        with self.assertRaises(Refused):
            self.start("plan")
        self.ledger.release(other)
        self.start("plan")
        self.finish("plan")

    def test_failure_cancels_all_owned_claims_and_reservations(self):
        self.start("plan")
        self.finish("plan")
        self.start("a")
        self.start("b")
        self.run.fail("a",self.ctx,self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        for w in ("plan","a","b"):
            with self.assertRaises(Refused):
                self.g.checkpoint(self.grants[w],self.run.request(w))
        with self.assertRaises(Refused):
            self.start("review")

    def test_stale_context_releases_owned_reservation(self):
        self.start("plan")
        with self.assertRaises(Refused):
            self.run.complete("plan",replace(self.ctx,generation=2),self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_capacity_shrink_does_not_fake_completion(self):
        self.start("plan")
        self.ledger.resize(1024)
        self.assertEqual(self.ledger.snapshot(),(1024,2048,0))
        self.finish("plan")
        with self.assertRaises(Refused):
            self.start("a")
        self.ledger.resize(4096)
        self.start("a")
        self.run.cancel()
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
