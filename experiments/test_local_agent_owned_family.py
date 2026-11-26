# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
import test_local_agent_owned_drain as base

class OwnedFamilyTests(unittest.TestCase):
    def setUp(self):
        f=base.ManagedWaveTests()
        f.setUp()
        self.f=f
        self.h=f.start("plan")
    def member(self,name):
        return self.f.run.register_member(self.h,name,self.f.ctx,self.f.models)
    def leave(self,m):
        return self.f.run.observe_member_exit(m,self.f.ctx,self.f.models)
    def test_root_first_keeps_memory_until_all_members_exit(self):
        a,b=self.member("a"),self.member("b")
        self.assertEqual(self.f.exit(self.h),"PENDING")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.leave(a),"PENDING")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.leave(b),"COMPLETED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))
    def test_members_first_does_not_release_root(self):
        a=self.member("a")
        self.assertEqual(self.leave(a),"PENDING")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.f.exit(self.h),"COMPLETED")
    def test_forged_duplicate_and_late_member_refused(self):
        a=self.member("a")
        with self.assertRaises(Refused):
            self.leave(replace(a))
        self.leave(a)
        with self.assertRaises(Refused):
            self.leave(a)
        with self.assertRaises(Refused):
            self.member("a")
        self.f.exit(self.h)
        with self.assertRaises(Refused):
            self.leave(a)
    def test_failed_root_revokes_but_keeps_reservation(self):
        a=self.member("a")
        self.assertEqual(self.f.exit(self.h,1),"PENDING")
        with self.assertRaises(Refused):
            self.f.g.checkpoint(self.f.grants["plan"],self.f.run.request("plan"))
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.leave(a),"FAILED")
        self.assertTrue(self.f.run.drained())
    def test_cancelled_family_discards_pending_payload(self):
        a=self.member("a")
        self.f.exit(self.h,payload=b"old")
        self.f.run.stop()
        self.assertEqual(self.leave(a),"DRAINED")
        with self.assertRaises(Refused):
            self.f.run.output("plan",self.f.ctx,self.f.models)
    def test_root_duplicate_and_member_after_root_denied(self):
        a=self.member("a")
        self.f.exit(self.h)
        with self.assertRaises(Refused):
            self.f.exit(self.h)
        with self.assertRaises(Refused):
            self.member("late")
        self.leave(a)

    def test_cancel_then_nonzero_exit_remains_drained(self):
        a=self.member("a")
        self.f.run.stop()
        self.assertEqual(self.f.exit(self.h,1),"PENDING")
        self.assertEqual(self.leave(a),"DRAINED")
    def test_stop_drops_retained_root_payload_immediately(self):
        a=self.member("a")
        self.f.exit(self.h,payload=b"private-fixture")
        self.f.run.stop()
        self.assertIsNone(self.f.run._root_exits["plan"][2])
        self.assertEqual(self.leave(a),"DRAINED")
    def test_stale_root_event_revokes_before_member_exit(self):
        a=self.member("a")
        stale=replace(self.f.ctx,generation=2)
        self.assertEqual(self.f.run.observe_exit(self.h,0,stale,self.f.models,payload=b"stale"),"PENDING")
        with self.assertRaises(Refused):
            self.f.g.checkpoint(self.f.grants["plan"],self.f.run.request("plan"))
        self.assertEqual(self.leave(a),"DRAINED")
    def test_stale_member_event_cannot_be_revived_by_fresh_root(self):
        a=self.member("a")
        stale=replace(self.f.ctx,generation=2)
        self.assertEqual(self.f.run.observe_member_exit(a,stale,self.f.models),"PENDING")
        self.assertEqual(self.f.exit(self.h),"DRAINED")
    def test_member_expiry_revokes_other_live_workers(self):
        a,b=self.member("a"),self.member("b")
        self.f.g.revoke("owned-plan")
        self.assertEqual(self.leave(a),"PENDING")
        self.assertEqual(self.f.exit(self.h),"PENDING")
        self.assertEqual(self.leave(b),"DRAINED")
