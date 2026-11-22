# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Gate tests: no session, provider or agent is contacted."""
import unittest

from tihulu_jarvis.agents import GATES, TOKENS, Gate, checked, closed
from tihulu_jarvis.voice.m1 import Hold


class GateTests(unittest.TestCase):
    def test_each_gate_names_a_closed_capability_and_its_reason(self):
        self.assertEqual(TOKENS, ("opencode_session_list", "provider_login",
                                  "agent_spawn", "remote_tool_call",
                                  "task_cost_reporting"))
        for gate in closed():
            with self.subTest(gate=gate.token):
                self.assertEqual(checked(gate), gate)
                self.assertTrue(gate.why)

    def test_an_unknown_gate_is_refused_rather_than_guessed_at(self):
        with self.assertRaises(Hold):
            checked(Gate("teleportation", "not this build"))
        for bad in ("opencode_session_list", None, 42, Gate("provider_login", "")):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    checked(bad)

    def test_the_gates_are_the_ones_the_page_states(self):
        self.assertEqual(closed(), GATES)
        self.assertEqual(len(closed()), len(TOKENS))
