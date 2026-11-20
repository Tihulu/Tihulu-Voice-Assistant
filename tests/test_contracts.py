# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
import unittest

from tihulu_jarvis.contracts import (
    Mode,
    ModelCard,
    ModelCategory,
    ModelSelection,
    Permission,
    Profile,
    VoiceControl,
    VoiceState,
)


class ContractTests(unittest.TestCase):
    def test_exactly_four_modes(self):
        self.assertEqual(
            {m.value for m in Mode},
            {"local_only", "cloud_chat", "secure_hybrid", "custom"},
        )

    def test_three_model_categories(self):
        self.assertEqual(
            {c.value for c in ModelCategory}, {"stt", "llm", "tts"}
        )

    def test_voice_control_events_exist(self):
        for control in (
            VoiceControl.TALK,
            VoiceControl.STOP_SPEAKING,
            VoiceControl.CANCEL_TURN,
            VoiceControl.END_SESSION,
            VoiceControl.HARD_STOP,
        ):
            self.assertIsInstance(control.value, str)

    def test_voice_states_include_speaking_and_interrupted(self):
        self.assertIn(VoiceState.SPEAKING, set(VoiceState))
        self.assertIn(VoiceState.INTERRUPTED, set(VoiceState))

    def test_selection_is_independent_per_category(self):
        selection = ModelSelection()
        self.assertIsNone(selection.stt)
        self.assertIsNone(selection.llm)
        self.assertIsNone(selection.tts)
        self.assertEqual(selection.voices, {})

    def test_profile_defaults_are_isolated(self):
        first = Profile("a", "A", Mode.CUSTOM, ModelSelection())
        second = Profile("b", "B", Mode.CUSTOM, ModelSelection())
        first.selection.voices["tr"] = "fixture-tr"
        self.assertEqual(second.selection.voices, {})
        self.assertNotEqual(first.profile_id, second.profile_id)

    def test_permission_rules_include_deny(self):
        self.assertEqual(
            {p.value for p in Permission}, {"deny", "ask", "scoped_allow"}
        )

    def test_model_card_records_compatibility(self):
        card = ModelCard(
            "fixture-local-chat",
            ModelCategory.LLM,
            "jan",
            "jan",
            ("tr", "en", "de"),
            True,
            True,
            "verified",
        )
        self.assertTrue(card.local)
        self.assertEqual(card.compatibility, "verified")


if __name__ == "__main__":
    unittest.main()
