# SPDX-License-Identifier: AGPL-3.0-only
"""The acceptance scoring must not hide missing or extra words."""
import unittest
from tools.voice_wav_acceptance import wer, words, language_recall, noisy_pcm, concatenate_clips


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


    def test_seeded_noise_is_reproducible_bounded_and_preserves_length(self):
        import math
        import struct
        source = struct.pack("<hh", 1000, -1000) * 8000
        a = noisy_pcm(source)
        self.assertEqual(a, noisy_pcm(source))
        self.assertNotEqual(a, noisy_pcm(source, seed=271))
        self.assertEqual(len(a), len(source))
        noise = [v[0]-orig[0] for v, orig in zip(struct.iter_unpack("<h", a),
                                                 struct.iter_unpack("<h", source))]
        rms = math.sqrt(sum(v*v for v in noise) / len(noise))
        self.assertAlmostEqual(20 * math.log10(1000/rms), 15, delta=.05)

    def test_noise_rejects_invalid_pcm_and_silence(self):
        for data in (b"", b"x", bytes(320)):
            with self.subTest(data_len=len(data)), self.assertRaises(ValueError):
                noisy_pcm(data)


    def test_fixture_gap_length_is_exact_and_original_samples_are_unchanged(self):
        import tempfile
        import wave
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "speech.wav"
            payload = b"\x01\x00" * 160
            with wave.open(str(source), "wb") as out:
                out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                out.writeframes(payload)
            target = root / "mixed.wav"
            for gap in (0, 500, 1000):
                concatenate_clips([source, source], target, gap_ms=gap)
                with wave.open(str(target), "rb") as inp:
                    self.assertEqual(inp.readframes(inp.getnframes()),
                                     (payload + bytes(gap * 32)) * 2)
            with self.assertRaises(ValueError):
                concatenate_clips([source], target, gap_ms=-1)
