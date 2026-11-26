# SPDX-License-Identifier: AGPL-3.0-only
"""The acceptance scoring must not hide missing or extra words."""
import unittest
from tools.voice_wav_acceptance import wer, words


class VoiceWavScoringTests(unittest.TestCase):
    def test_case_and_punctuation_are_normalized(self):
        self.assertEqual(wer("Guten Morgen!", "guten, morgen."), 0)
        self.assertEqual(words("Go\u0308kyu\u0308zu\u0308"), ["gökyüzü"])

    def test_insertion_deletion_and_substitution(self):
        self.assertEqual(wer("one two", "one"), .5)
        self.assertEqual(wer("one two", "one three"), .5)
        self.assertEqual(wer("one two", "one two three"), .5)
        self.assertEqual(wer("one", "two three four"), 3)

    def test_empty_output_is_not_a_pass(self):
        self.assertEqual(wer("one two", ""), 1)
        with self.assertRaises(ValueError):
            wer("", "one")
