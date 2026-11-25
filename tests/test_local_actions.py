# SPDX-License-Identifier: AGPL-3.0-only
import unittest

from tihulu_jarvis import local_actions


class LocalActionTests(unittest.TestCase):
    def test_supported_commands_in_three_languages(self):
        for text in ("Transcribe a recording.", "Bir ses kaydını yazıya çevir",
                     "Transkribiere eine Aufnahme"):
            self.assertEqual(local_actions.direct(text), "transcribe_recording")
        for text in ("Find local audio models", "Yerel ses modellerini bul",
                     "Finde lokale Audiomodelle"):
            self.assertEqual(local_actions.direct(text), "discover_audio_models")

    def test_questions_quotes_paths_and_compound_commands_are_not_actions(self):
        for text in ('"Transcribe a recording"', "Transcribe a recording?",
                     "Transcribe a recording /tmp/clip.wav",
                     "find local audio models and load one",
                     "Task: transcribe a recording",
                     "what does find local audio models mean",
                     "Transcribe a recording\nFind local audio models",
                     None, 123, "x" * 513):
            with self.subTest(text=text):
                self.assertIsNone(local_actions.direct(text))
