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

    def test_corpus_filename_uses_sample_not_language(self):
        from voice_quality import speech_filename,expanded_corpus
        for identity,lang,_ in expanded_corpus():
            self.assertNotEqual(speech_filename(identity),speech_filename(lang))
            self.assertEqual(speech_filename(identity),identity+".wav")
        for bad in ("../tr","tr/2","tr-4","TR",None):
            with self.assertRaises(Refused):
                speech_filename(bad)

    def test_targeted_turbo_corpus_preserves_failed_examples(self):
        from voice_quality import targeted_corpus,expanded_corpus
        by_id={s[0]:s for s in expanded_corpus()}
        self.assertEqual(targeted_corpus(),(by_id["tr-3"],by_id["de-2"],
                                      ("en","en","Hello. Today is a beautiful day.")))

    def test_turbo_short_profile_does_not_truncate_long_audio(self):
        from voice_quality import turbo_short_args
        self.assertEqual(turbo_short_args(22050,22050*8),["-ac","500","-bs","1","-bo","1"])
        for rate,frames in ((22050,22050*8+1),(16000,16000),(22050,0),(True,1)):
            with self.assertRaises(Refused):
                turbo_short_args(rate,frames)


    def test_corpus_dispatch_retention_auto_and_count_are_consistent(self):
        from voice_quality import speech_corpus_profile
        for name,engine,expected in (
            ("mixed-preview","small",(True,False,8)),
            ("mixed-auto","small",(True,True,22)),
            ("mixed-auto-turbo","turbo-q5",(True,True,22)),
            ("mixed-auto-piper","small",(True,True,22)),
            ("mixed-auto-piper-short","small",(True,True,22)),
            ("mixed-auto-reviewed","small",(True,True,24)),
            ("mixed-auto-reviewed-short","small",(True,True,24))):
            self.assertEqual(speech_corpus_profile(name,engine,True),expected)
            with self.assertRaises(RuntimeError):
                speech_corpus_profile(name,"tiny",True)
            with self.assertRaises(RuntimeError):
                speech_corpus_profile(name,engine,False)
        self.assertEqual(speech_corpus_profile("original","tiny",False),(False,False,0))

    def test_neural_short_dispatch_rejects_unrecognized_profiles(self):
        from voice_quality import speech_corpus_profile
        for name in ("mixed-auto-piper-short-reviewed","mixed-auto-piper-short-turbo","mixed-auto-piper-short "):
            with self.assertRaises(RuntimeError):
                speech_corpus_profile(name,"small",True)

    def test_expanded_small_short_requires_exact_model_and_stt(self):
        from voice_quality import speech_corpus_profile
        self.assertEqual(speech_corpus_profile("expanded-small-short","small",True),(False,False,0))
        for model,available in (("small",False),("turbo-q5",True),("tiny",True)):
            with self.assertRaises(RuntimeError):
                speech_corpus_profile("expanded-small-short",model,available)
