# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""State-token tests: no widget, device or connection is used."""
import unittest

from tihulu_jarvis import states
from tihulu_jarvis.disclosure import facts
from tihulu_jarvis.profiles import Profile, Rule
from tihulu_jarvis.states import (APPROVAL_PENDING, CLOUD, MICROPHONE_ACTIVE, LOCAL,
                                  OFFLINE, PRIVILEGED_TOOL, approval_pending,
                                  microphone, network, privileged, privacy,
                                  tokens)


def profile(**kw):
    values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
    values.update(kw)
    return Profile(**values).checked()


def grants_of(profile):
    from tihulu_jarvis.profiles import granted
    return granted(profile)


class TokenTests(unittest.TestCase):
    def test_privacy_follows_the_boundary_not_the_mode_name(self):
        self.assertEqual(privacy(facts(profile(), "http://127.0.0.1:9876/")), LOCAL)
        cloud = facts(profile(based_on_mode="CLOUD_CHAT"), "http://127.0.0.1:9876/")
        self.assertEqual(privacy(cloud), CLOUD)
        denied = facts(profile(based_on_mode="CLOUD_CHAT",
                              tool_rules=(Rule("cloud_inference", ("x",), "deny"),
                                          Rule("cloud_disclosure", ("x",), "deny"))),
                       "http://127.0.0.1:9876/")
        self.assertEqual(privacy(denied), LOCAL)

    def test_the_microphone_state_comes_from_the_stage(self):
        self.assertEqual(microphone("listening"), "listening")
        self.assertEqual(microphone("transcribing"), "transcribing")
        for stage in ("idle", "thinking", "speaking", "stopping", "synthesizing"):
            with self.subTest(stage=stage):
                self.assertEqual(microphone(stage), "idle")

    def test_a_privileged_profile_says_so_in_a_word(self):
        self.assertEqual(privileged(frozenset()), "")
        self.assertEqual(privileged(frozenset({"host_shell"})), PRIVILEGED_TOOL)

    def test_offline_and_pending_are_words_not_colours(self):
        self.assertEqual(network(False), OFFLINE)
        self.assertEqual(network(True), "")
        self.assertEqual(approval_pending(False), "")
        self.assertEqual(approval_pending(True), APPROVAL_PENDING)

    def test_the_tokens_are_ordered_and_without_empties(self):
        data = facts(profile(based_on_mode="CLOUD_CHAT"), "http://127.0.0.1:9876/")
        out = tokens(data, "listening", frozenset({"host_shell"}), True, False)
        self.assertEqual(out, (CLOUD, MICROPHONE_ACTIVE, "listening",
                               PRIVILEGED_TOOL, APPROVAL_PENDING, OFFLINE))
        quiet = tokens(facts(profile(), "http://127.0.0.1:9876/"),
                      "ready", frozenset(), False, True)
        self.assertEqual(quiet, (LOCAL, "idle"))


class MutedTokenTests(unittest.TestCase):
    def test_muted_is_a_word_not_a_colour(self):
        self.assertEqual(states.muted(True), states.MUTED)
        self.assertEqual(states.muted(False), "")

    def test_the_muted_token_joins_the_row(self):
        data = facts(profile(), "http://127.0.0.1:9876/")
        quiet = states.tokens(data, "ready", frozenset(), False, True, True)
        self.assertIn(states.MUTED, quiet)
        loud = states.tokens(data, "ready", frozenset(), False, True, False)
        self.assertNotIn(states.MUTED, loud)
        self.assertEqual(loud, (states.LOCAL, "idle"))
