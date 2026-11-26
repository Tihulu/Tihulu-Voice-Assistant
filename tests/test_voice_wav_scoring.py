# SPDX-License-Identifier: AGPL-3.0-only
"""The acceptance scoring must not hide missing or extra words."""
import unittest
from tools.voice_wav_acceptance import wer, words, language_recall


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


    def test_global_wer_can_hide_complete_loss_of_one_language(self):
        refs = [("tr", "merhaba dünya"), ("en", "the sky is blue"),
                ("de", "der himmel ist blau")]
        observed = "the sky is blue der himmel ist blau"
        self.assertEqual(wer(" ".join(text for _, text in refs), observed), .2)
        self.assertEqual(language_recall(refs, observed), {"tr": 0, "en": 1, "de": 1})

    def test_alignment_cannot_reuse_a_word_for_two_languages(self):
        refs = [("en", "one test"), ("de", "test zwei")]
        self.assertEqual(sum(language_recall(refs, "test").values()), .5)
        with self.assertRaises(ValueError):
            language_recall([("tr", "")], "merhaba")
