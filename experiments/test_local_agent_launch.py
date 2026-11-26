# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_launch import LaunchBoundary
import test_local_agent_owned_drain as base

class LaunchBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f=base.ManagedWaveTests()
        self.f.setUp()
        self.b=LaunchBoundary(self.f.run)
    def prepare(self):
        return self.b.prepare("plan",self.f.grants["plan"],self.f.ctx,self.f.models)
    def start(self,t):
        self.b.started(t,self.f.ctx,self.f.models)
    def reap(self,t):
        return self.b.observe_reaped(t,0,self.f.ctx,self.f.models,payload=b"ok")
    def test_definite_no_start_releases_and_revokes(self):
        t=self.prepare()
        self.assertEqual(self.b.not_started(t,self.f.ctx,self.f.models),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))
        with self.assertRaises(Refused):
            self.start(t)
    def test_unknown_never_releases_from_timeout_or_fake_exit(self):
        t=self.prepare()
        self.b.uncertain(t)
        self.assertEqual(self.b.state(t),"UNKNOWN")
        for fn in (lambda:self.reap(t),lambda:self.b.not_started(t,self.f.ctx,self.f.models),lambda:self.start(t)):
            with self.assertRaises(Refused):
                fn()
        self.b.stop()
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertFalse(self.f.run.drained())
    def test_stop_between_prepare_and_start_keeps_owned_running_ticket(self):
        t=self.prepare()
        self.b.stop()
        with self.assertRaises(Refused):
            self.start(t)
        self.assertEqual(self.b.state(t),"RUNNING")
        self.assertEqual(self.reap(t),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))
    def test_stale_started_event_does_not_lose_reservation(self):
        t=self.prepare()
        with self.assertRaises(Refused):
            self.b.started(t,replace(self.f.ctx,generation=2),self.f.models)
        self.assertEqual(self.b.state(t),"RUNNING")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.reap(t),"DRAINED")
    def test_forged_ticket_and_invalid_exit_do_not_mutate(self):
        t=self.prepare()
        with self.assertRaises(Refused):
            self.start(replace(t))
        self.start(t)
        with self.assertRaises(Refused):
            self.b.observe_reaped(t,True,self.f.ctx,self.f.models)
        self.assertEqual(self.b.state(t),"RUNNING")
        self.assertEqual(self.reap(t),"COMPLETED")
        with self.assertRaises(Refused):
            self.reap(t)
    def test_cannot_read_inputs_until_started(self):
        t=self.prepare()
        with self.assertRaises(Refused):
            self.b.inputs(t,self.f.ctx,self.f.models)
        self.start(t)
        self.assertEqual(self.b.inputs(t,self.f.ctx,self.f.models),())
        self.b.stop()
        with self.assertRaises(Refused):
            self.b.inputs(t,self.f.ctx,self.f.models)
        self.reap(t)
