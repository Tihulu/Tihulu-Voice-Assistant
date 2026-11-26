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

    def test_cloud_cannot_disclose_publish_or_initiate_egress(self):
        for action in ("research", "catalog", "transfer", "provider", "disclose", "publish"):
            for actor in ("cloud", "owner"):
                with self.subTest(action=action, actor=actor), self.assertRaises(Refused):
                    self.gateway().fixture_grant("x", Request(self.ctx, action, "target", actor, "public"), 1)

    def test_provider_payload_classification_and_mode(self):
        for mode in ("CLOUD_CHAT", "STANDARD_HYBRID", "SECURE_HYBRID"):
            ctx = replace(self.ctx, mode=mode)
            for kind in ("none", "private", "secret", "minimized", "public"):
                g = self.gateway(ctx)
                req = Request(ctx, "provider", "target", "local", kind)
                if kind == "public" or mode == "SECURE_HYBRID" and kind == "minimized":
                    self.assertEqual(g.checkpoint(g.fixture_grant("x", req, 5), req), "SIMULATED_ALLOW")
                else:
                    with self.assertRaises(Refused):
                        g.fixture_grant("x", req, 5)

    def test_invalid_rules_rejected_before_evaluation(self):
        for rule in (profiles.Rule("unknown"), profiles.Rule("filesystem_read", (), "invalid"),
                     profiles.Rule("filesystem_read", ("x", "x"), "allow"),
                     profiles.Rule("filesystem_read", ["x"], "allow"),
                     profiles.Rule("filesystem_read", ("x",), "allow", "", float("nan")),
                     profiles.Rule("filesystem_read", ("x",), "allow", "approval", 0)):
            with self.subTest(rule=rule), self.assertRaises(Refused):
                self.gateway(rules=(rule,))

    def test_timed_rule_expiry_latches_grant(self):
        g = self.gateway(rules=(profiles.Rule("filesystem_read", ("target",), "allow", "fixture", 105),))
        req = Request(self.ctx, "engine", "target", "local")
        grant = g.fixture_grant("x", req, 30)
        self.wall = 106
        with self.assertRaises(Refused):
            g.checkpoint(grant, req)
        self.wall = 101
        with self.assertRaises(Refused):
            g.checkpoint(grant, req)

    def test_context_cannot_be_rebound_through_public_attribute(self):
        g=self.gateway()
        with self.assertRaises(AttributeError):
            g.context=replace(self.ctx,mode="CLOUD_CHAT")
        self.assertEqual(g.context,self.ctx)

    def test_broad_explicit_deny_is_not_overridden_by_narrow_allow(self):
        g=self.gateway(rules=(profiles.Rule("filesystem_read",(),"deny"),
                              profiles.Rule("filesystem_read",("target",),"allow")))
        with self.assertRaisesRegex(Refused,"explicit_deny"):
            g.fixture_grant("x",Request(self.ctx,"engine","target","local"),10)

    def test_capture_requires_audio_specific_permission(self):
        req=Request(self.ctx,"capture","target","local","private")
        with self.assertRaises(Refused):
            self.gateway(rules=(profiles.Rule("filesystem_read",("target",),"allow"),)).fixture_grant("x",req,10)
        g=self.gateway(rules=(profiles.Rule("local_audio_device",("target",),"allow"),))
        self.assertEqual(g.checkpoint(g.fixture_grant("x",req,10),req),"SIMULATED_ALLOW")
        with self.assertRaises(Refused):
            g.fixture_grant("y",replace(req,classification="public"),10)

    def test_browser_requires_both_policy_ceilings(self):
        req=Request(self.ctx,"browser","target","local","public")
        net=profiles.Rule("external_network",("target",),"allow")
        browser=profiles.Rule("browser_navigate",("target",),"allow")
        for rules in ((net,),(browser,)):
            with self.assertRaises(Refused):
                self.gateway(rules=rules).fixture_grant("x",req,10)
        g=self.gateway(rules=(net,browser))
        self.assertEqual(g.checkpoint(g.fixture_grant("x",req,10),req),"SIMULATED_ALLOW")
        with self.assertRaises(Refused):
            g.fixture_grant("y",replace(req,actor="cloud"),10)

    def test_dependent_claim_requires_claimed_exact_parent(self):
        g = self.gateway()
        parent_req = Request(self.ctx, "engine", "target", "local")
        child_req = Request(self.ctx, "publish", "target", "local")
        parent = g.fixture_grant("parent", parent_req, 20)
        child = g.fixture_grant("child", child_req, 20)
        with self.assertRaisesRegex(Refused, "dependency_not_claimed"):
            g.claim_dependent(child, child_req, parent, parent_req)
        g.claim(parent, parent_req)
        with self.assertRaisesRegex(Refused, "request_mismatch"):
            g.claim_dependent(child, child_req, parent, child_req)
        self.assertEqual(g.claim_dependent(child, child_req, parent, parent_req),
                         "SIMULATED_ALLOW")
        with self.assertRaisesRegex(Refused, "grant_already_claimed"):
            g.claim_dependent(child, child_req, parent, parent_req)

    def test_dependent_claim_rejects_revoked_parent_without_consuming_child(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        parent = g.fixture_grant("parent", r, 20)
        child = g.fixture_grant("child", r, 20)
        g.claim(parent, r)
        g.revoke("parent")
        with self.assertRaisesRegex(Refused, "unknown_or_revoked_grant"):
            g.claim_dependent(child, r, parent, r)
        # Diagnostic only: no real effect, proves failed pair did not claim child.
        self.assertEqual(g.claim(child, r), "SIMULATED_ALLOW")

    def test_dependent_claim_refuses_expired_parent_and_same_grant(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        parent = g.fixture_grant("parent", r, 5)
        child = g.fixture_grant("child", r, 20)
        g.claim(parent, r)
        with self.assertRaisesRegex(Refused, "dependency_not_claimed"):
            g.claim_dependent(parent, r, parent, r)
        self.wall = 106
        with self.assertRaisesRegex(Refused, "expired"):
            g.claim_dependent(child, r, parent, r)

    def test_dependent_claim_refuses_foreign_or_revoked_child(self):
        g = self.gateway()
        other = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        parent = g.fixture_grant("parent", r, 20)
        g.claim(parent, r)
        foreign = other.fixture_grant("child", r, 20)
        with self.assertRaisesRegex(Refused, "unknown_or_revoked_grant"):
            g.claim_dependent(foreign, r, parent, r)
        child = g.fixture_grant("child", r, 20)
        g.revoke("child")
        with self.assertRaisesRegex(Refused, "unknown_or_revoked_grant"):
            g.claim_dependent(child, r, parent, r)

    def test_multi_dependency_claim_rejects_revoked_second_parent(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        a, b, child = [g.fixture_grant(n, r, 20) for n in ("a", "b", "child")]
        g.claim(a, r)
        g.claim(b, r)
        g.revoke("b")
        with self.assertRaises(Refused):
            g.claim_with_dependencies(child, r, ((a,r),(b,r)))
        self.assertEqual(g.claim(child,r),"SIMULATED_ALLOW")

    def test_group_requires_distinct_claimed_grants(self):
        g = self.gateway()
        r = Request(self.ctx, "engine", "target", "local")
        a, b = [g.fixture_grant(n,r,20) for n in ("a","b")]
        g.claim(a,r)
        for group in ((), ((a,r),(a,r)), ((a,r),(b,r)), [(a,r)]):
            with self.assertRaises(Refused):
                g.checkpoint_claimed_group(group)
        g.claim(b,r)
        self.assertEqual(g.checkpoint_claimed_group(((a,r),(b,r))),"SIMULATED_ALLOW")

    def test_group_checks_all_deadlines_with_single_clock(self):
        g = self.gateway()
        r = Request(self.ctx,"engine","target","local")
        a = g.fixture_grant("a",r,5)
        b = g.fixture_grant("b",r,20)
        g.claim(a,r)
        g.claim(b,r)
        self.wall = 106
        with self.assertRaisesRegex(Refused,"expired"):
            g.checkpoint_claimed_group(((b,r),(a,r)))

if __name__ == "__main__":
    unittest.main()
