# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_resources import ResourceLedger
from local_agent_replan import AttemptCoordinator
import test_local_agent_wave_run as fixtures

class ManagedReplanTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.WaveRunTests()
        f.setUp()
        self.ctx,self.models,self.g=f.ctx,f.models,f.g
        self.ledger=ResourceLedger(4096)
        self.c=AttemptCoordinator(self.g,self.ledger,"drain",f.workers,4096,2,owned_drain=True)
        self.run=self.c.confirm(self.c.prepare(self.ctx,self.models),self.ctx,self.models)
        self.grants={w.identity:self.g.fixture_grant("drain-"+w.identity,self.run.request(w.identity),60)
                     for w in f.workers}

    def start(self,w):
        return self.run.start(w,self.grants[w],self.ctx,self.models)

    def exit(self,h,code=0):
        return self.run.observe_exit(h,code,self.ctx,self.models,payload=b"ok")

    def test_replan_denied_until_all_owned_exits_observed(self):
        self.exit(self.start("plan"))
        a,b=self.start("a"),self.start("b")
        self.assertEqual(self.c.stop(),(a,b))
        with self.assertRaises(Refused):
            self.c.prepare(self.ctx,self.models)
        self.exit(a)
        with self.assertRaises(Refused):
            self.c.prepare(self.ctx,self.models)
        self.exit(b)
        self.assertTrue(self.run.drained())
        fresh=self.c.confirm(self.c.prepare(self.ctx,self.models),self.ctx,self.models)
        self.assertNotEqual(fresh.request("plan"),self.run.request("plan"))
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_failure_requires_sibling_drain_before_replan(self):
        self.exit(self.start("plan"))
        a,b=self.start("a"),self.start("b")
        self.assertEqual(self.exit(a,1),"FAILED")
        with self.assertRaises(Refused):
            self.c.prepare(self.ctx,self.models)
        self.exit(b)
        self.c.confirm(self.c.prepare(self.ctx,self.models),self.ctx,self.models)

    def test_context_change_closes_but_does_not_release_live_reservation(self):
        h=self.start("plan")
        with self.assertRaises(Refused):
            self.c.prepare(replace(self.ctx,generation=2),self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.exit(h),"DRAINED")
        with self.assertRaises(Refused):
            self.c.prepare(self.ctx,self.models)

    def test_capacity_pressure_cannot_mask_stale_context(self):
        self.exit(self.start("plan"))
        a=self.start("a")
        self.ledger.resize(2048)
        with self.assertRaises(Refused):
            self.run.start("b",self.grants["b"],replace(self.ctx,generation=2),self.models)
        self.assertEqual(self.ledger.snapshot(),(2048,2048,0))
        with self.assertRaises(Refused):
            self.g.checkpoint(self.grants["a"],self.run.request("a"))
        self.assertEqual(self.exit(a),"DRAINED")

    def test_stop_before_start_allows_fresh_preview(self):
        self.assertEqual(self.c.stop(),())
        self.c.confirm(self.c.prepare(self.ctx,self.models),self.ctx,self.models)
