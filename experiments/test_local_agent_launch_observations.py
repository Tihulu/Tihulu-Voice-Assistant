# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_launch import LaunchBoundary,LaunchObservation
import test_local_agent_owned_drain as base

class LaunchObservationTests(unittest.TestCase):
    def setUp(self):
        self.f=base.ManagedWaveTests()
        self.f.setUp()
        self.b=LaunchBoundary(self.f.run)
        self.t=self.b.prepare("plan",self.f.grants["plan"],self.f.ctx,self.f.models)
    def start(self):
        self.b.started(self.t,self.f.ctx,self.f.models)
    def member(self):
        return self.b.register_member(self.t,"fixed",self.f.ctx,self.f.models)
    def leave(self,m):
        return self.b.observe_member_reaped(self.t,m,self.f.ctx,self.f.models)
    def reap(self):
        return self.b.observe_reaped(self.t,0,self.f.ctx,self.f.models,payload=b"ok")
    def test_unknown_visible_even_after_stop(self):
        self.assertEqual(self.b.observations(),(LaunchObservation("plan","PREPARED"),))
        self.b.uncertain(self.t)
        self.b.stop()
        self.assertEqual(self.b.observations(),(LaunchObservation("plan","UNKNOWN"),))
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
    def test_root_reaped_state_retains_member_and_denies_input(self):
        self.start()
        m=self.member()
        self.assertEqual(self.reap(),"PENDING")
        self.assertEqual(self.b.observations(),(LaunchObservation("plan","ROOT_REAPED"),))
        with self.assertRaises(Refused):
            self.b.inputs(self.t,self.f.ctx,self.f.models)
        with self.assertRaises(Refused):
            self.reap()
        self.assertEqual(self.leave(m),"COMPLETED")
        self.assertEqual(self.b.observations(),())
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))
    def test_member_first_then_root_clears_ticket(self):
        self.start()
        m=self.member()
        self.assertEqual(self.leave(m),"PENDING")
        self.assertEqual(self.reap(),"COMPLETED")
        self.assertEqual(self.b.observations(),())
    def test_foreign_member_cannot_release(self):
        self.start()
        m=self.member()
        self.reap()
        with self.assertRaises(Refused):
            self.leave(replace(m))
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.leave(m)
    def test_cancelled_family_waits_and_discards_output(self):
        self.start()
        m=self.member()
        self.reap()
        self.b.stop()
        self.assertEqual(self.leave(m),"DRAINED")
        self.assertEqual(self.b.observations(),())
        with self.assertRaises(Refused):
            self.f.run.output("plan",self.f.ctx,self.f.models)
    def test_known_member_blocks_ambiguous_root_transition(self):
        self.start()
        m=self.member()
        with self.assertRaises(Refused):
            self.b.uncertain(self.t)
        self.b.stop()
        self.leave(m)
        self.assertEqual(self.reap(),"DRAINED")
