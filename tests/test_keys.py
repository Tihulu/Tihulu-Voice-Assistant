# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Keybinding table tests: no keyboard is simulated."""
import unittest

from tihulu_jarvis.keys import BINDINGS, checked, names
from tihulu_jarvis.voice.m1 import Hold


class TableTests(unittest.TestCase):
    def test_the_table_is_short_and_complete(self):
        self.assertEqual(names(), ("send", "focus_search", "cancel_turn",
                                   "hard_stop", "attach"))
        self.assertEqual(len(BINDINGS), len(set(names())))

    def test_a_binding_names_its_action_target_and_key(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding[0]):
                self.assertEqual(checked(binding), binding)
                self.assertTrue(binding[1])
                self.assertTrue(binding[3])

    def test_an_unknown_action_or_target_is_refused(self):
        with self.assertRaisesRegex(Hold, "unknown_key_action"):
            checked(("format_disk", "Wipe", "window", "F1", ()))
        with self.assertRaisesRegex(Hold, "unknown_key_target"):
            checked(("send", "Send", "provider", "F1", ()))
        for trigger in ("", None, 42, []):
            with self.subTest(trigger=trigger):
                with self.assertRaisesRegex(Hold, "invalid_key_trigger"):
                    checked(("send", "Send", "entry", trigger, ()))
