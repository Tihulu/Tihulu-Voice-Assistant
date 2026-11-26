# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from gateway import Refused
from voice_quality import word_errors,acceptable

class VoiceQualityTests(unittest.TestCase):
    def test_unicode_and_punctuation(self):
        self.assertEqual(word_errors("İSTANBUL, IŞIK!","istanbul ışık","tr"),(0,2))
        self.assertEqual(word_errors("Guten Tag. Schön.","guten tag schön","de"),(0,3))
    def test_insertion_deletion_substitution(self):
        self.assertEqual(word_errors("one two three","one two four five","en"),(2,3))
        self.assertEqual(word_errors("one two three","","en"),(3,3))
        self.assertEqual(word_errors("one two three","one three","en"),(1,3))
    def test_predeclared_gate_and_wrong_language(self):
        self.assertTrue(acceptable(2,5))
        self.assertFalse(acceptable(3,5))
        self.assertFalse(acceptable(0,2))
        self.assertFalse(acceptable(True,5))
        with self.assertRaises(Refused):
            word_errors("one","one","auto")
    def test_bounds(self):
        with self.assertRaises(Refused):
            word_errors("x "*65,"x","en")
        with self.assertRaises(Refused):
            word_errors("x","x "*129,"en")

    def test_expanded_corpus_balanced_and_distinct(self):
        from voice_quality import expanded_corpus,words
        samples=expanded_corpus()
        self.assertEqual(len(samples),6)
        self.assertEqual(len({s[0] for s in samples}),6)
        self.assertEqual({lang:sum(s[1]==lang for s in samples) for lang in ("tr","en","de")},
                         {"tr":2,"en":2,"de":2})
        for identity,lang,text in samples:
            self.assertTrue(identity.startswith(lang+"-"))
            self.assertGreaterEqual(len(words(text,lang)),3)
