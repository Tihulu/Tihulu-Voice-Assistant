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
