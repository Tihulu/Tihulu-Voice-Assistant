import hashlib
import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from market_fixture import Manifest, FixtureRegistry, FixtureTransfer
from transfer_resume import ResumeBook

class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("r5", "session", "profile", 1, "route", "LOCAL_ONLY", False, True)
        self.data = b"synthetic-model-fixture"
        self.m = Manifest("model", "r1", "weights.bin", len(self.data), hashlib.sha256(self.data).hexdigest(), "fixture-license")
        self.g = FixtureGateway(self.ctx, (profiles.Rule("external_network", (), "allow"),), clock=profiles.PolicyClock())
        self.req = Request(self.ctx, "transfer", self.m.identity, "local", "public")
        self.old = self.g.fixture_grant("old", self.req, 60)
        self.new = self.g.fixture_grant("new", self.req, 60)
        self.t = FixtureTransfer(self.g, self.old, self.ctx, self.m)
        self.t.append(self.m.identity, 0, self.data[:5])
        self.book = ResumeBook(self.g,self.ctx)
        self.receipt, self.prefix = self.book.pause(self.t)

    def test_resume_fresh_grant_and_exact_prefix(self):
        t = self.book.resume(self.new, self.m, self.receipt, self.prefix)
        t.append(self.m.identity, 5, self.data[5:])
        r = FixtureRegistry()
        t.finish(r)
        self.assertEqual(r.current("model"), self.m.identity)
        with self.assertRaises(Refused):
            self.t.append(self.m.identity, 5, self.data[5:])

    def test_old_grant_and_mutation_rejected(self):
        for grant, prefix in ((self.old, self.prefix), (self.new, b"x"*5)):
            with self.assertRaises(Refused):
                self.book.resume(grant, self.m, self.receipt, prefix)

    def test_wrong_revision_or_offset_rejected(self):
        with self.assertRaises(Refused):
            self.book.resume(self.new, replace(self.m, revision="r2"), self.receipt, self.prefix)
        with self.assertRaises(Refused):
            self.book.resume(self.new, self.m, replace(self.receipt, offset=True), self.prefix)

    def test_revoked_resume_and_commit(self):
        t = self.book.resume(self.new, self.m, self.receipt, self.prefix)
        t.append(self.m.identity, 5, self.data[5:])
        self.g.revoke("new")
        with self.assertRaises(Refused):
            t.finish(FixtureRegistry())
        with self.assertRaises(Refused):
            self.book.resume(self.new, self.m, self.receipt, self.prefix)

    def test_forged_old_grant_and_prefix_refused(self):
        forged=replace(self.receipt,old_grant="fabricated")
        with self.assertRaises(Refused):
            self.book.resume(self.old,self.m,forged,self.prefix)
        forged=replace(self.receipt,prefix_digest=hashlib.sha256(b"x"*5).hexdigest())
        with self.assertRaises(Refused):
            self.book.resume(self.new,self.m,forged,b"x"*5)
        with self.assertRaises(Refused):
            self.book.resume(self.new,self.m,replace(self.receipt),self.prefix)

    def test_receipt_consumed_once_and_cross_book_refused(self):
        other=ResumeBook(self.g,self.ctx)
        with self.assertRaises(Refused):
            other.resume(self.new,self.m,self.receipt,self.prefix)
        self.book.resume(self.new,self.m,self.receipt,self.prefix)
        with self.assertRaises(Refused):
            self.book.resume(self.new,self.m,self.receipt,self.prefix)

    def test_closed_book_rejects_receipt(self):
        self.book.close()
        with self.assertRaises(Refused):
            self.book.resume(self.new,self.m,self.receipt,self.prefix)

    def test_failed_resume_retires_claimed_grant_and_keeps_receipt(self):
        from unittest.mock import patch
        original=FixtureTransfer.append
        def fail(t,*args):
            original(t,*args)
            raise Refused("fixture_interrupted")
        with patch.object(FixtureTransfer,"append",fail):
            with self.assertRaises(Refused):
                self.book.resume(self.new,self.m,self.receipt,self.prefix)
        with self.assertRaises(Refused):
            self.g.checkpoint(self.new,self.req)
        fresh=self.g.fixture_grant("fresh",self.req,60)
        t=self.book.resume(fresh,self.m,self.receipt,self.prefix)
        t.append(self.m.identity,len(self.prefix),self.data[len(self.prefix):])
        r=FixtureRegistry()
        t.finish(r)
        self.assertEqual(r.current_bytes("model"),self.data)
