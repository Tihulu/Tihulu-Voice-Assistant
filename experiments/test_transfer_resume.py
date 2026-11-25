import hashlib
import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from market_fixture import Manifest, FixtureRegistry, FixtureTransfer
from transfer_resume import pause, resume

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
        self.receipt, self.prefix = pause(self.t)

    def test_resume_fresh_grant_and_exact_prefix(self):
        t = resume(self.g, self.new, self.ctx, self.m, self.receipt, self.prefix)
        t.append(self.m.identity, 5, self.data[5:])
        r = FixtureRegistry()
        t.finish(r)
        self.assertEqual(r.current("model"), self.m.identity)
        with self.assertRaises(Refused):
            self.t.append(self.m.identity, 5, self.data[5:])

    def test_old_grant_and_mutation_rejected(self):
        for grant, prefix in ((self.old, self.prefix), (self.new, b"x"*5)):
            with self.assertRaises(Refused):
                resume(self.g, grant, self.ctx, self.m, self.receipt, prefix)

    def test_wrong_revision_or_offset_rejected(self):
        with self.assertRaises(Refused):
            resume(self.g, self.new, self.ctx, replace(self.m, revision="r2"), self.receipt, self.prefix)
        with self.assertRaises(Refused):
            resume(self.g, self.new, self.ctx, self.m, replace(self.receipt, offset=True), self.prefix)

    def test_revoked_resume_and_commit(self):
        t = resume(self.g, self.new, self.ctx, self.m, self.receipt, self.prefix)
        t.append(self.m.identity, 5, self.data[5:])
        self.g.revoke("new")
        with self.assertRaises(Refused):
            t.finish(FixtureRegistry())
        with self.assertRaises(Refused):
            resume(self.g, self.new, self.ctx, self.m, self.receipt, self.prefix)
