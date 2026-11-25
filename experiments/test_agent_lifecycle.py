import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from agent_lifecycle import FixtureAgent

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("r4", "session", "profile", 1, "route", "SECURE_HYBRID")
        self.g = FixtureGateway(self.ctx, (profiles.Rule("host_shell", ("opencode-fixture",), "allow"),),
                                clock=profiles.PolicyClock())
        self.grant = self.g.fixture_grant("agent", Request(self.ctx, "agent", "opencode-fixture", "local"), 60)
        self.a = FixtureAgent(self.g, self.grant, self.ctx, "opencode-fixture", "s1", "owner")

    def test_owned_job_lifecycle(self):
        h = self.a.submit("j1")
        self.a.event(h, 1, "progress")
        self.a.event(h, 2, "completed")
        self.assertEqual(self.a.state, "completed")
        with self.assertRaises(Refused):
            self.a.event(h, 3, "completed")
        with self.assertRaises(Refused):
            self.a.submit("j1")

    def test_no_double_submit_or_forged_handle(self):
        h = self.a.submit("j1")
        with self.assertRaises(Refused):
            self.a.submit("j2")
        with self.assertRaises(Refused):
            self.a.cancel(replace(h))
        self.assertEqual(self.a.state, "running")

    def test_revoke_discards_late_event(self):
        h = self.a.submit("j1")
        self.a.event(h, 1, "progress")
        self.g.revoke("agent")
        with self.assertRaises(Refused):
            self.a.event(h, 2, "completed")
        self.assertEqual(self.a.state, "held")
        self.assertEqual(self.a.events, ())

    def test_cancel_after_revoke(self):
        h = self.a.submit("j1")
        self.g.revoke("agent")
        self.a.cancel(h)
        self.assertEqual(self.a.state, "cancelled")
        with self.assertRaises(Refused):
            self.a.submit("j2")

    def test_order_and_type_are_strict(self):
        h = self.a.submit("j1")
        for seq, kind in ((True, "progress"), (2, "progress"), (1, "unknown")):
            with self.assertRaises(Refused):
                self.a.event(h, seq, kind)
        self.a.event(h, 1, "progress")
        self.assertEqual(self.a.events, ((1, "progress"),))
