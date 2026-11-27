# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from gateway import Refused
from local_agent_replan import AttemptCoordinator
from local_agent_resources import ResourceLedger
import test_local_agent_wave_run as base

class StrictCoordinatorTests(unittest.TestCase):
    def setUp(self):
        f=base.WaveRunTests()
        f.setUp()
        self.f=f
        self.ledger=ResourceLedger(4096)
        self.c=AttemptCoordinator(f.g,self.ledger,"strict-managed",f.workers,4096,2,
                                  owned_drain=True,strict_results=True)
        self.preview=self.c.prepare(f.ctx,f.models)
        self.run=self.c.confirm(self.preview,f.ctx,f.models)
        self.grants={w.identity:f.g.fixture_grant("strict-managed-"+w.identity,self.run.request(w.identity),60)
                     for w in f.workers}
    def start(self,w):
        return self.run.start(w,self.grants[w],self.f.ctx,self.f.models)
    def done(self,h,payload=b"result"):
        return self.run.observe_exit(h,0,self.f.ctx,self.f.models,payload=payload)
    def inputs(self,h):
        return self.run.inputs(h,self.f.ctx,self.f.models)
    def test_preview_discloses_strict_policy_and_survives_replan(self):
        self.assertIs(self.preview.strict_results,True)
        self.c.stop()
        p=self.c.prepare(self.f.ctx,self.f.models)
        self.assertIs(p.strict_results,True)
        r=self.c.confirm(p,self.f.ctx,self.f.models)
        self.assertNotEqual(r.request("plan"),self.run.request("plan"))
    def test_missing_dependency_read_terminally_drains(self):
        self.done(self.start("plan"))
        a,b=self.start("a"),self.start("b")
        with self.assertRaises(Refused):
            self.done(a)
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        with self.assertRaises(Refused):
            self.c.prepare(self.f.ctx,self.f.models)
        self.assertEqual(self.done(b),"DRAINED")
        self.c.prepare(self.f.ctx,self.f.models)
    def test_root_missing_payload_drains(self):
        with self.assertRaises(Refused):
            self.done(self.start("plan"),None)
        self.assertTrue(self.run.drained())
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
    def test_full_strict_managed_chain(self):
        self.done(self.start("plan"),b"plan")
        for w in ("a","b"):
            h=self.start(w)
            self.assertEqual(self.inputs(h)[0][1],b"plan")
            self.done(h,w.encode())
        h=self.start("review")
        self.assertEqual(tuple(p for r,p in self.inputs(h)),(b"a",b"b"))
        self.done(h,b"review")
        r,p=self.run.output("review",self.f.ctx,self.f.models)
        self.assertEqual(p,b"review")
        self.assertEqual(len(r.input_digest),64)
    def test_strict_policy_cannot_use_legacy_coordinator(self):
        f=self.f
        with self.assertRaises(Refused):
            AttemptCoordinator(f.g,self.ledger,"bad",f.workers,4096,2,strict_results=True)
