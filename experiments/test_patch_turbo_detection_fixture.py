# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from patch_turbo_detection_fixture import ANCHOR,INSERT,patched_source

class TurboDetectionSourcePatchTests(unittest.TestCase):
    def test_only_one_insertion_before_detection(self):
        original="before\n"+ANCHOR+"after\nstate->exp_n_audio_ctx = params.audio_ctx;\n"
        patched=patched_source(original)
        self.assertEqual(patched.replace(INSERT,"",1),original)
        self.assertIn("params.detect_language && params.audio_ctx == 500",INSERT)
        self.assertIn("n_samples > 8 * WHISPER_SAMPLE_RATE",INSERT)
        self.assertLess(patched.index(INSERT),patched.index(ANCHOR))
    def test_unknown_or_already_patched_source_is_denied(self):
        later="state->exp_n_audio_ctx = params.audio_ctx;\n"
        for bad in ("",ANCHOR,ANCHOR+ANCHOR+later,later+ANCHOR,ANCHOR+later+later,INSERT+ANCHOR+later):
            with self.assertRaises(ValueError):
                patched_source(bad)
