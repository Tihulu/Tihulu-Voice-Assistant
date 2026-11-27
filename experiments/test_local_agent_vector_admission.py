# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_planner import Worker
from local_agent_vector_resources import Capacity,Demand,VectorResources
from local_agent_vector_admission import VectorAdmission
import test_local_agent_vector_planner as fixtures

class VectorAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.VectorPlannerTests()
        self.f.setUp()
        self.ledger=VectorResources(Capacity(64,16))
        workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),0,"execute"),
                 Worker("b",("plan",),0,"execute"),Worker("review",("a","b"),0,"review"))
        self.a=VectorAdmission("owner",self.ledger,workers,limit=2)
    def prepare(self):
        return self.a.prepare(self.f.ctx,self.f.models,self.f.mem)
    def confirm(self,p):
        self.a.confirm(p,self.f.ctx,self.f.models,self.f.mem)
    def reserve(self,p):
        return self.a.reserve_next(p,self.f.ctx,self.f.models,self.f.mem)
    def begin(self):
        p=self.prepare()
        self.confirm(p)
        return p
    def finish(self,r):
        self.a.observe_reaped(r,success=True)
    def test_full_wave_barriers(self):
        p=self.begin()
        r,=self.reserve(p)
        with self.assertRaises(Refused):
            self.reserve(p)
        self.finish(r)
        a,b=self.reserve(p)
        self.finish(a)
        with self.assertRaises(Refused):
            self.reserve(p)
        self.finish(b)
        self.finish(self.reserve(p)[0])
        self.assertTrue(self.a.completed(p))
        self.assertEqual(self.ledger.snapshot()[1],Capacity(0,0))
    def test_stale_offer_and_changed_capacity(self):
        p=self.prepare()
        q=self.prepare()
        for fake in (p,replace(q)):
            with self.assertRaises(Refused):
                self.confirm(fake)
        self.ledger.resize(Capacity(64,15))
        with self.assertRaises(Refused):
            self.confirm(q)
        with self.assertRaises(Refused):
            self.confirm(q)
    def test_live_contention_refuses_entire_wave(self):
        p=self.begin()
        self.finish(self.reserve(p)[0])
        external,=self.ledger.reserve_wave("other",(Demand("x",Capacity(1,9)),))
        with self.assertRaises(Refused):
            self.reserve(p)
        self.assertEqual(self.ledger.snapshot()[1],Capacity(1,9))
        self.ledger.release_reaped(external)
        with self.assertRaises(Refused):
            self.reserve(p)
        q=self.begin()
        self.assertNotEqual(p.identity,q.identity)
    def test_cancel_keeps_active_and_prevents_replan(self):
        p=self.begin()
        self.finish(self.reserve(p)[0])
        a,b=self.reserve(p)
        self.a.stop()
        self.assertEqual(self.ledger.snapshot()[1],Capacity(4,8))
        with self.assertRaises(Refused):
            self.prepare()
        self.finish(a)
        with self.assertRaises(Refused):
            self.prepare()
        self.finish(b)
        self.assertFalse(self.a.completed(p))
        self.begin()
    def test_failed_peer_retains_other_ownership(self):
        p=self.begin()
        self.finish(self.reserve(p)[0])
        a,b=self.reserve(p)
        self.a.observe_reaped(a,success=False)
        self.assertEqual(self.ledger.snapshot()[1],Capacity(2,4))
        with self.assertRaises(Refused):
            self.reserve(p)
        self.finish(b)
        self.assertFalse(self.a.completed(p))
    def test_inventory_change_stops_attempt(self):
        p=self.begin()
        changed=(replace(self.f.models[0],rank=99),)+self.f.models[1:]
        with self.assertRaises(Refused):
            self.a.reserve_next(p,self.f.ctx,changed,self.f.mem)
        self.assertEqual(self.ledger.snapshot()[1],Capacity(0,0))
        with self.assertRaises(Refused):
            self.reserve(p)
    def test_exact_terminal_handle_and_close(self):
        p=self.begin()
        r,=self.reserve(p)
        with self.assertRaises(Refused):
            self.finish(replace(r))
        self.a.close()
        with self.assertRaises(Refused):
            self.prepare()
        self.assertEqual(self.ledger.snapshot()[1],Capacity(2,4))
        self.finish(r)
        self.assertEqual(self.ledger.snapshot()[1],Capacity(0,0))
    def test_no_offer_no_reservation(self):
        self.assertFalse(self.a.completed(None))
        with self.assertRaises(Refused):
            self.reserve(None)
    def test_context_change_before_confirmation(self):
        p=self.prepare()
        with self.assertRaises(Refused):
            self.a.confirm(p,replace(self.f.ctx,generation=2),self.f.models,self.f.mem)
        self.assertEqual(self.ledger.snapshot()[1],Capacity(0,0))
