import unittest
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from task_chain import Chain, Step

class ChainTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("r4", "session", "profile", 1, "route", "SECURE_HYBRID")
        self.g = FixtureGateway(self.ctx, (profiles.Rule("filesystem_read", (), "allow"),), clock=profiles.PolicyClock())
        self.steps = (Step("a", "compute", "csv", "table"), Step("b", "render", "table", "chart"))
        self.a = self.g.fixture_grant("a", Request(self.ctx, "engine", "compute", "local"), 60)
        self.b = self.g.fixture_grant("b", Request(self.ctx, "engine", "render", "local"), 60)

    def test_chain_lineage_and_separate_grants(self):
        c = Chain(self.g, self.ctx, self.steps)
        a = c.complete(c.begin(self.a, "csv"), "table", b"table")
        with self.assertRaises(Refused):
            c.begin(self.a, "table")
        b = c.complete(c.begin(self.b, "table"), "chart", b"chart")
        self.assertEqual(b.parent, a.digest)
        self.assertEqual(c.state, "completed")

    def test_invalid_chain_and_artifact(self):
        with self.assertRaises(Refused):
            Chain(self.g, self.ctx, (self.steps[1], self.steps[0]))
        c = Chain(self.g, self.ctx, self.steps)
        h = c.begin(self.a, "csv")
        with self.assertRaises(Refused):
            c.complete(h, "chart", b"wrong")
        self.assertEqual(c.state, "held")

    def test_revoke_and_stale_handle(self):
        c = Chain(self.g, self.ctx, self.steps)
        h = c.begin(self.a, "csv")
        self.g.revoke("a")
        with self.assertRaises(Refused):
            c.complete(h, "table", b"late")
        self.assertEqual(c.state, "held")
        with self.assertRaises(Refused):
            c.complete(h, "table", b"late")

    def test_cancel_prevents_late_publication(self):
        c = Chain(self.g, self.ctx, self.steps)
        h = c.begin(self.a, "csv")
        c.cancel()
        with self.assertRaises(Refused):
            c.complete(h, "table", b"late")
        self.assertEqual(c.state, "cancelled")
