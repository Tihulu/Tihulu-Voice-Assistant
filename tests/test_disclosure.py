# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Disclosure tests: no endpoint is contacted and no content is inspected."""
import unittest

from tihulu_jarvis.disclosure import (CLOUD_WITH_APPROVAL, CLOUD_WITHOUT_APPROVAL,
                                      STAYS_LOCAL, boundary, endpoint_scope, facts,
                                      statement)
from tihulu_jarvis.profiles import Profile, Rule


def profile(**kw):
    values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
    values.update(kw)
    return Profile(**values).checked()


LOOPBACK = "http://127.0.0.1:9876/"  # The Jan parser takes no path.


class BoundaryTests(unittest.TestCase):
    def test_a_local_mode_stays_local_even_with_local_rules(self):
        busy = profile(shell_rules=(Rule("host_shell", ("jan-qwen",), "allow", "owner-1", 1e12),),
                       background_processing_enabled=True, voice_activation="wake_word")
        self.assertEqual(boundary(busy), (STAYS_LOCAL, "mode_grants_no_cloud"))

    def test_a_cloud_mode_without_providers_states_the_gap(self):
        self.assertEqual(boundary(profile(based_on_mode="CLOUD_CHAT")),
                         (CLOUD_WITHOUT_APPROVAL, "no_approved_provider"))

    def test_a_cloud_mode_with_providers_names_the_permission(self):
        chat = profile(based_on_mode="CLOUD_CHAT", allowed_providers=("fixture-provider",))
        self.assertEqual(boundary(chat), (CLOUD_WITH_APPROVAL, "approved_providers_listed"))

    def test_a_denied_cloud_rule_returns_the_boundary_to_local(self):
        denied = profile(based_on_mode="CLOUD_CHAT",
                         tool_rules=(Rule("cloud_inference", ("fixture-provider",), "deny"),
                                     Rule("cloud_disclosure", ("fixture-provider",), "deny")))
        self.assertEqual(boundary(denied), (STAYS_LOCAL, "mode_grants_no_cloud"))


class EndpointScopeTests(unittest.TestCase):
    def test_only_a_numeric_loopback_address_is_loopback(self):
        self.assertEqual(endpoint_scope(LOOPBACK), "loopback")
        for raw in ("http://localhost:9876/x", "http://192.0.2.10:80/x",
                    "http://example.test/x", "", None, 42):
            with self.subTest(raw=raw):
                self.assertEqual(endpoint_scope(raw), "not_loopback")


class FactsTests(unittest.TestCase):
    def test_facts_carry_no_address_or_secret(self):
        data = facts(profile(based_on_mode="CLOUD_CHAT", allowed_providers=("fixture-provider",)),
                     LOOPBACK)
        text = repr(data)
        self.assertNotIn("9876", text)
        self.assertNotIn("127.0.0.1", text)
        self.assertEqual(data["providers"], ("fixture-provider",))
        self.assertIn("cloud_inference", data["grants"])
        self.assertEqual(data["transport"], "loopback_only")

    def test_direct_transport_does_not_prove_upstream_locality(self):
        for mode in ("LOCAL_ONLY", "CLOUD_CHAT", "SECURE_HYBRID", "CUSTOM"):
            for providers in ((), ("fixture-provider",)):
                with self.subTest(mode=mode, providers=providers):
                    data = facts(profile(based_on_mode=mode, allowed_providers=providers),
                                 LOOPBACK)
                    self.assertIsNone(data["content_leaves"])
                    self.assertFalse(data["direct_remote_inference"])
                    self.assertFalse(data["upstream_verified"])
                    self.assertEqual(data["leak_reason"], "loopback_upstream_unverified")

    def test_clock_floor_controls_disclosure_grants(self):
        timed = profile(based_on_mode="SECURE_HYBRID",
                        shell_rules=(Rule("host_shell", ("jan-qwen",), "allow",
                                          "fixture", 110),))
        self.assertIn("host_shell", facts(timed, LOOPBACK, 100)["grants"])
        self.assertNotIn("host_shell", facts(timed, LOOPBACK, 120)["grants"])
        self.assertNotIn("host_shell", facts(timed, LOOPBACK, float("inf"))["grants"])

    def test_a_non_loopback_endpoint_is_stated_not_implied(self):
        data = facts(profile(), "http://example.test/x")
        self.assertEqual(data["endpoint"], "not_loopback")
        self.assertIn("refuses it", statement(data))

    def test_the_statement_matches_the_boundary(self):
        self.assertEqual(statement(facts(profile(), LOOPBACK)),
                         "loopback transport only; upstream processing is unverified")
        approved = facts(profile(based_on_mode="CLOUD_CHAT",
                                 allowed_providers=("fixture-provider",)), LOOPBACK)
        self.assertIn("permits cloud reasoning", statement(approved))
        self.assertIn("refused", statement(facts(profile(based_on_mode="CLOUD_CHAT"), LOOPBACK)))
        # A malformed address is a scope, never an exception at this layer.
        self.assertEqual(endpoint_scope("not-a-url"), "not_loopback")
