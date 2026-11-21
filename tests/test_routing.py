# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Routing tests: no engine, endpoint or model is used."""
import unittest

from tihulu_jarvis.profiles import Profile
from tihulu_jarvis.routing import Preference, Route, describe, permitted, select
from tihulu_jarvis.voice.m1 import Hold


def profile(**kw):
    values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
    values.update(kw)
    return Profile(**values).checked()


LOCAL = Route("llm", "fixture-local-chat", "local", "jan", "http://127.0.0.1:9876")
CLOUD = Route("llm", "fixture-provider-chat", "cloud", "fixture-provider")


class PermissionTests(unittest.TestCase):
    def test_a_local_route_is_always_local(self):
        for mode in ("LOCAL_ONLY", "CLOUD_CHAT", "SECURE_HYBRID", "CUSTOM"):
            with self.subTest(mode=mode):
                self.assertEqual(permitted(profile(based_on_mode=mode), LOCAL), (True, "local_route"))

    def test_local_only_refuses_a_cloud_route(self):
        self.assertEqual(permitted(profile(), CLOUD), (False, "local_only_forbids_cloud_route"))

    def test_a_cloud_route_needs_an_approved_provider(self):
        chat = profile(based_on_mode="CLOUD_CHAT")
        self.assertEqual(permitted(chat, CLOUD), (True, "mode_permits_cloud_route"))
        scoped = profile(based_on_mode="CLOUD_CHAT", allowed_providers=("other-provider",))
        self.assertEqual(permitted(scoped, CLOUD), (False, "approved_provider_required"))


class SelectionTests(unittest.TestCase):
    def test_the_first_permitted_route_is_chosen(self):
        pref = Preference("llm", (LOCAL, CLOUD))
        route, reason = select(profile(), pref)
        self.assertEqual(route, LOCAL)
        self.assertEqual(reason, "local_route")
        self.assertIn("this computer", describe(route, reason))

    def test_an_unavailable_local_route_is_not_replaced_silently(self):
        pref = Preference("llm", (CLOUD,))
        with self.assertRaisesRegex(Hold, "local_only_forbids_cloud_route"):
            select(profile(), pref)

    def test_an_ordered_fallback_inside_an_allowed_mode_is_explicit(self):
        pref = Preference("llm", (CLOUD, LOCAL))
        route, reason = select(profile(based_on_mode="CLOUD_CHAT"), pref)
        self.assertEqual(route, CLOUD)
        self.assertIn("mode_permits_cloud_route", reason)

    def test_an_empty_preference_is_honest(self):
        with self.assertRaisesRegex(Hold, "no_route_configured"):
            select(profile(), Preference("llm", ()))

    def test_a_route_for_another_task_is_refused(self):
        with self.assertRaisesRegex(Hold, "route_task_mismatch"):
            Preference("stt", (LOCAL,)).checked()
        with self.assertRaisesRegex(Hold, "unknown_task"):
            Preference("vision", ()).checked()
        with self.assertRaisesRegex(Hold, "unknown_task"):
            Route("vision", "x", "local").checked()
        with self.assertRaisesRegex(Hold, "approved_provider_required"):
            Route("llm", "x", "cloud", "unknown").checked()
