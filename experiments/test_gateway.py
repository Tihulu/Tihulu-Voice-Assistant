# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context, Request, Grant, FixtureGateway, Refused, ACTIONS


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task", "session", "profile", 1, "route", "SECURE_HYBRID", False)
        self.rules = tuple(profiles.Rule(a, ("target",), "allow") for a in set(ACTIONS.values()))
        self.wall = 100.0
        self.mono = 50.0
        self.p1 = patch.object(profiles.time, "time", side_effect=lambda: self.wall)
        self.p2 = patch.object(profiles.time, "monotonic", side_effect=lambda: self.mono)
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def gateway(self, ctx=None, rules=None):
        return FixtureGateway(ctx or self.ctx, self.rules if rules is None else rules,
                              clock=profiles.PolicyClock(self.wall, self.mono))

    def test_exact_grant_and_context(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        grant = g.fixture_grant("id", r, 10)
        self.assertEqual(g.checkpoint(grant, r), "SIMULATED_ALLOW")
        for changed in (replace(r, target="other"), replace(r, actor="owner"),
                        replace(r, context=replace(self.ctx, generation=2)),
                        replace(r, context=replace(self.ctx, task="other"))):
            with self.assertRaises(Refused):
                g.checkpoint(grant, changed)
        with self.assertRaises(Refused):
            g.checkpoint(replace(grant), r)

    def test_wall_rollback_does_not_revive(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        grant = g.fixture_grant("id", r, 10)
        self.wall = 120
        self.mono = 51
        with self.assertRaisesRegex(Refused, "expired"):
            g.checkpoint(grant, r)
        self.wall = 101
        self.mono = 52
        with self.assertRaises(Refused):
            g.checkpoint(grant, r)
        with self.assertRaises(Refused):
            g.fixture_grant("id", r, 10)

    def test_monotonic_expiry_despite_wall_rollback(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        grant = g.fixture_grant("id", r, 10)
        self.wall = 90
        self.mono = 61
        with self.assertRaises(Refused):
            g.checkpoint(grant, r)

    def test_revoke_and_close(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        a = g.fixture_grant("a", r, 10)
        b = g.fixture_grant("b", r, 10)
        g.revoke("a")
        with self.assertRaises(Refused):
            g.checkpoint(a, r)
        g.close()
        with self.assertRaises(Refused):
            g.checkpoint(b, r)

    def test_mode_matrix(self):
        for mode in ("LOCAL_ONLY", "CLOUD_CHAT", "STANDARD_HYBRID", "SECURE_HYBRID"):
            ctx = replace(self.ctx, mode=mode)
            for actor in ("cloud", "owner", "local"):
                r = Request(ctx, "engine", "target", actor)
                g = self.gateway(ctx)
                if actor == "local" and mode != "CLOUD_CHAT":
                    self.assertEqual(g.checkpoint(g.fixture_grant("x", r, 1), r), "SIMULATED_ALLOW")
                else:
                    with self.assertRaises(Refused):
                        g.fixture_grant("x", r, 1)

    def test_offline_and_local_public_opt_in(self):
        for ctx in (replace(self.ctx, offline=True), replace(self.ctx, mode="LOCAL_ONLY")):
            g = self.gateway(ctx)
            with self.assertRaises(Refused):
                g.fixture_grant("a", Request(ctx, "research", "target", "local", "public"), 1)
        ctx = replace(self.ctx, mode="LOCAL_ONLY", public_opt_in=True)
        g = self.gateway(ctx)
        r = Request(ctx, "research", "target", "local", "public")
        self.assertEqual(g.checkpoint(g.fixture_grant("a", r, 1), r), "SIMULATED_ALLOW")
        with self.assertRaises(Refused):
            g.fixture_grant("b", Request(ctx, "provider", "target", "local", "public"), 1)

    def test_disclosure_and_denial(self):
        for kind in ("private", "secret", "none"):
            with self.assertRaises(Refused):
                self.gateway().fixture_grant("a", Request(self.ctx, "disclose", "target", "local", kind), 1)
        g = self.gateway()
        r = Request(self.ctx, "disclose", "target", "local", "minimized")
        self.assertEqual(g.checkpoint(g.fixture_grant("a", r, 1), r), "SIMULATED_ALLOW")
        g = self.gateway(rules=self.rules + (profiles.Rule("filesystem_read", ("target",), "deny"),))
        with self.assertRaises(Refused):
            g.fixture_grant("a", Request(self.ctx, "engine", "target", "local"), 1)

    def test_bad_deadline_unknown_scope_and_generation(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        for seconds in (True, 0, -1, float("nan"), float("inf"), 3601):
            with self.assertRaises(Refused):
                g.fixture_grant("id", r, seconds)
        with self.assertRaises(Refused):
            g.fixture_grant("id", replace(r, target="missing"), 1)
        with self.assertRaises(Refused):
            replace(self.ctx, generation=True).checked()

if __name__ == "__main__":
    unittest.main()
