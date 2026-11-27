# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from heterogeneous_model_owned_review import OwnedMixedReview
import test_heterogeneous_model_review as base

class OwnedReviewTests(unittest.TestCase):
    def setUp(self):
        self.f=base.MixedModelReviewTests()
        self.f.setUp()
        self.r=OwnedMixedReview(self.f.r)
        self.args=self.f.args
        self.ledger=self.f.f.ledger
    def prepare(self):
        return self.r.prepare(self.f.g,*self.args)
    def dispatch(self):
        t,w=self.prepare()
        self.r.dispatch(t,w,*self.args)
        return t,w
    def test_real_reservation_lifetime_and_exact_usage_receipt(self):
        t,w=self.dispatch()
        self.assertEqual(self.ledger.snapshot(),(4096,16,4080))
        self.assertEqual(self.r.usage(),(16,0,16,0))
        self.assertEqual(self.r.observe_reaped(t,0,self.f.envelope(w),*self.args),"COMPLETED")
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        self.assertEqual(self.r.usage(),(16,16,0,0))
        self.assertEqual(self.r.output(*self.args)[1],b"bird")
    def test_cancel_retains_reservation_until_exact_family_exit(self):
        t,w=self.dispatch()
        self.r.stop()
        self.assertIs(self.r.pending(),t)
        self.assertEqual(self.ledger.snapshot()[1],16)
        self.assertEqual(self.r.observe_reaped(t,0,self.f.envelope(w),*self.args),"DRAINED")
        self.assertEqual(self.r.usage(),(16,16,0,0))
        with self.assertRaises(Refused):
            self.r.output(*self.args)
    def test_no_spawn_voids_undispatched_budget(self):
        t,w=self.prepare()
        self.r.not_started(t)
        self.assertEqual(self.r.usage(),(16,0,0,16))
        self.assertEqual(self.ledger.snapshot()[1],0)
    def test_forged_ticket_and_invalid_exit_do_not_release(self):
        t,w=self.dispatch()
        for ticket,code in ((replace(t),0),(t,True),(t,256)):
            with self.assertRaises(Refused):
                self.r.observe_reaped(ticket,code,self.f.envelope(w),*self.args)
        self.assertEqual(self.ledger.snapshot()[1],16)
        self.assertEqual(self.r.observe_reaped(t,1,None,*self.args),"FAILED")
        self.assertEqual(self.r.usage(),(16,16,0,0))
    def test_revocation_after_dispatch_charges_actual_and_denies_output(self):
        t,w=self.dispatch()
        self.f.f.g.revoke("grant-csv")
        with self.assertRaises(Refused):
            self.r.observe_reaped(t,0,self.f.envelope(w),*self.args)
        self.assertEqual(self.r.usage(),(16,16,0,0))
        self.assertEqual(self.ledger.snapshot()[1],0)
        with self.assertRaises(Refused):
            self.r.output(*self.args)
    def test_bad_wire_pre_dispatch_requires_definite_absence(self):
        t,w=self.prepare()
        with self.assertRaises(Refused):
            self.r.dispatch(t,w+b" ",*self.args)
        self.assertEqual(self.ledger.snapshot()[1],16)
        self.r.not_started(t)
        self.assertEqual(self.r.usage(),(16,0,0,16))
    def test_malformed_reply_charged_full_after_exit(self):
        t,w=self.dispatch()
        with self.assertRaises(Refused):
            self.r.observe_reaped(t,0,b"bad",*self.args)
        self.assertEqual(self.r.usage(),(16,16,0,0))
        self.assertEqual(self.ledger.snapshot()[1],0)
    def test_duplicate_owner_and_invalid_budget_refused(self):
        with self.assertRaises(Refused):
            OwnedMixedReview(self.f.r)
        with self.assertRaises(Refused):
            OwnedMixedReview(self.f.r,generated_budget=True)
