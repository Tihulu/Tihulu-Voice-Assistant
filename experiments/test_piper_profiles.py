# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from gateway import Refused
from piper_engine_acceptance import voice_profile

class PiperProfileTests(unittest.TestCase):
    def test_closed_distinct_profiles(self):
        profiles=[voice_profile(lang) for lang in ("tr","en","de")]
        self.assertEqual(len({p[0] for p in profiles}),3)
        self.assertEqual([p[2] for p in profiles],["tr","en-us","de"])
        self.assertTrue(all(len(p[0])==64 for p in profiles))
    def test_unknown_profile_denied(self):
        for lang in ("auto","tr-en-de","../en","EN",None,[]):
            with self.assertRaises(Refused):
                voice_profile(lang)
