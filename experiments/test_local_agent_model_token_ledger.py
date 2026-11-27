# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_model_token_ledger import TokenLedger
from local_agent_model_fixture_contract import wire_for
from test_local_agent_model_fixture_contract import row

class TokenLedgerTests(unittest.TestCase):
    def test_budget_and_parallel_reservations(self):
        l=TokenLedger(48)
        p=wire_for("plan",())
        a=wire_for("a",(row("plan"),))
        b=wire_for("b",(row("plan"),))
        x=l.reserve("plan",p)
        l.settle(x,p,(1,)*16)
        y=l.reserve("a",a)
        z=l.reserve("b",b)
        self.assertEqual(l.snapshot(),(48,16,32,0))
        with self.assertRaises(Refused):
            l.reserve("review",wire_for("review",(row("a"),row("b"))))
        l.settle(y,a,(1,)*8)
        l.settle(z,b,(2,)*16)
        self.assertEqual(l.snapshot(),(48,40,0,8))
    def test_foreign_duplicate_and_wrong_wire_denied(self):
        l=TokenLedger(64)
        p=wire_for("plan",())
        x=l.reserve("plan",p)
        with self.assertRaises(Refused):
            l.settle(replace(x),p,(1,))
        with self.assertRaises(Refused):
            l.settle(x,p+b" ",(1,))
        l.settle(x,p,(1,))
        with self.assertRaises(Refused):
            l.reserve("plan",p)
        with self.assertRaises(Refused):
            l.settle(x,p,(1,))
        self.assertEqual(l.snapshot(),(64,1,0,63))
    def test_unknown_charged_full_and_close_idempotent(self):
        l=TokenLedger(32)
        x=l.reserve("plan",wire_for("plan",()))
        l.abandon(x)
        l.reserve("a",wire_for("a",(row("plan"),)))
        l.close()
        l.close()
        self.assertEqual(l.snapshot(),(32,32,0,0))
        with self.assertRaises(Refused):
            l.reserve("b",wire_for("b",(row("plan"),)))
    def test_invalid_usage_does_not_release(self):
        l=TokenLedger(16)
        w=wire_for("plan",())
        x=l.reserve("plan",w)
        for t in ((),(True,),(512,),(1,)*17,[1]):
            with self.assertRaises(Refused):
                l.settle(x,w,t)
        self.assertEqual(l.snapshot(),(16,0,16,0))
        l.close()
        self.assertEqual(l.snapshot(),(16,16,0,0))
    def test_invalid_budget(self):
        for b in (True,0,65,16.0):
            with self.assertRaises(Refused):
                TokenLedger(b)

    def test_strict_dispatch_exact_wire_and_once_only(self):
        l=TokenLedger(32,strict_dispatch=True)
        w=wire_for("plan",())
        x=l.reserve("plan",w)
        with self.assertRaises(Refused):
            l.settle(x,w,(1,))
        with self.assertRaises(Refused):
            l.dispatch(x,w,w+b" ")
        l.dispatch(x,w,w)
        with self.assertRaises(Refused):
            l.dispatch(x,w,w)
        with self.assertRaises(Refused):
            l.void(x)
        l.settle(x,w,(1,2))
        self.assertEqual(l.snapshot(),(32,2,0,30))
    def test_stream_reservation_binds_derived_transport(self):
        from local_agent_model_stream_contract import stream_wire
        l=TokenLedger(32,strict_dispatch=True)
        w=wire_for("plan",())
        x=l.reserve("plan",w,streaming=True)
        with self.assertRaises(Refused):
            l.dispatch(x,w,w)
        l.dispatch(x,w,stream_wire("plan",w))
        with self.assertRaises(Refused):
            l.settle(x,w,(1,))
        l.abandon(x)
        self.assertEqual(l.snapshot(),(32,16,0,16))
    def test_no_dispatch_void_does_not_charge_or_allow_replay(self):
        l=TokenLedger(32,strict_dispatch=True)
        w=wire_for("plan",())
        x=l.reserve("plan",w)
        l.void(x)
        self.assertEqual(l.snapshot(),(32,0,0,32))
        with self.assertRaises(Refused):
            l.reserve("plan",w)
    def test_close_distinguishes_unsent_and_uncertain_usage(self):
        l=TokenLedger(32,strict_dispatch=True)
        p=wire_for("plan",())
        a=wire_for("a",(row("plan"),))
        x=l.reserve("plan",p)
        l.reserve("a",a)
        l.dispatch(x,p,p)
        l.close()
        self.assertEqual(l.snapshot(),(32,16,0,16))
    def test_stream_requires_strict_policy(self):
        w=wire_for("plan",())
        with self.assertRaises(Refused):
            TokenLedger(16).reserve("plan",w,streaming=True)
        with self.assertRaises(Refused):
            TokenLedger(16,strict_dispatch=1)
    def test_foreign_lease_never_marks_dispatched(self):
        l=TokenLedger(16,strict_dispatch=True)
        w=wire_for("plan",())
        x=l.reserve("plan",w)
        with self.assertRaises(Refused):
            l.dispatch(replace(x),w,w)
        l.close()
        self.assertEqual(l.snapshot(),(16,0,0,16))

    def test_wave_preflight_rejects_whole_group_without_mutation(self):
        l=TokenLedger(32,strict_dispatch=True)
        p=wire_for("plan",())
        x=l.reserve("plan",p)
        l.dispatch(x,p,p)
        l.settle(x,p,(1,)*16)
        before=l.snapshot()
        with self.assertRaises(Refused):
            l.preflight(("a","b"))
        self.assertEqual(l.snapshot(),before)
        l.preflight(("a",))
        self.assertEqual(l.snapshot(),before)
    def test_wave_preflight_exact_roles_and_no_replay(self):
        l=TokenLedger(64,strict_dispatch=True)
        for group in ((),("a","a"),("unknown",),["a"],(True,)):
            with self.assertRaises(Refused):
                l.preflight(group)
        l.preflight(("a","b"))
        l.reserve("a",wire_for("a",(row("plan"),)))
        with self.assertRaises(Refused):
            l.preflight(("a","b"))
        l.close()
        with self.assertRaises(Refused):
            l.preflight(("b",))
    def test_preflight_is_not_a_reservation(self):
        l=TokenLedger(16,strict_dispatch=True)
        l.preflight(("plan",))
        l.reserve("a",wire_for("a",(row("plan"),)))
        with self.assertRaises(Refused):
            l.reserve("plan",wire_for("plan",()))

    def test_economy_profile_whole_wave_and_exact_lease_ceiling(self):
        from local_agent_model_fixture_contract import ECONOMY_CEILINGS
        l=TokenLedger(48,strict_dispatch=True,ceilings=ECONOMY_CEILINGS)
        p=wire_for("plan",())
        x=l.reserve("plan",p)
        l.dispatch(x,p,p)
        l.settle(x,p,(1,)*16)
        l.preflight(("a","b","review"))
        with self.assertRaises(Refused):
            l.reserve("a",wire_for("a",(row("plan"),)))
        a=wire_for("a",(row("plan"),),ceiling=8)
        x=l.reserve("a",a)
        l.dispatch(x,a,a)
        with self.assertRaises(Refused):
            l.settle(x,a,(1,)*9)
        l.settle(x,a,(1,)*8)
        self.assertEqual(l.snapshot(),(48,24,0,24))
    def test_economy_uncertain_charges_only_approved_ceiling(self):
        from local_agent_model_fixture_contract import ECONOMY_CEILINGS
        l=TokenLedger(48,strict_dispatch=True,ceilings=ECONOMY_CEILINGS)
        w=wire_for("a",(row("plan"),),ceiling=8)
        x=l.reserve("a",w)
        l.dispatch(x,w,w)
        l.close()
        self.assertEqual(l.snapshot(),(48,8,0,40))
    def test_invalid_ceiling_profiles(self):
        for profile in ((8,8,8,16),(16,True,8,16),(16,8,8),[16,8,8,16]):
            with self.assertRaises(Refused):
                TokenLedger(48,strict_dispatch=True,ceilings=profile)
        with self.assertRaises(Refused):
            TokenLedger(48,ceilings=(16,8,8,16))
