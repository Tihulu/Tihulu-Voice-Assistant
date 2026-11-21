# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Settings tests: local preferences only, no device or network."""
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis import settings
from tihulu_jarvis.voice.m1 import Hold


class SpokenTextTests(unittest.TestCase):
    def test_the_preference_is_a_plain_boolean(self):
        self.assertFalse(settings.empty()["show_spoken_text"])
        self.assertFalse(settings.checked({})["show_spoken_text"])
        self.assertTrue(settings.checked({"show_spoken_text": True})["show_spoken_text"])
        for bad in ("yes", 1, 0, None, [], {}):
            with self.subTest(bad=bad):
                # A truthy guess is not a preference; it is refused.
                with self.assertRaises(Hold):
                    settings.checked({"show_spoken_text": bad})

    def test_a_stored_choice_survives_a_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            cfg = settings.empty()
            cfg["show_spoken_text"] = True
            settings.save(cfg, path)
            self.assertTrue(settings.load(path)["show_spoken_text"])


class TextScaleTests(unittest.TestCase):
    def test_a_scale_is_one_of_the_two(self):
        self.assertEqual(settings.empty()["text_scale"], "normal")
        self.assertEqual(settings.checked({})["text_scale"], "normal")
        for bad in ("huge", "", "NORMAL", 12):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    settings.checked({"text_scale": bad})


class WebTargetTests(unittest.TestCase):
    def test_targets_are_entries_with_their_submission_right(self):
        self.assertEqual(settings.empty()["web_targets"], [])
        entry = {"url": "https://example.test/docs", "submit": False}
        self.assertEqual(settings.checked({"web_targets": [entry]})["web_targets"], [entry])
        for bad in ([{"url": "http://example.test/x"}], [{"url": "https://localhost/x"}],
                    ["https://example.test/docs"], [{"url": 5}], [{"url": "https://a.test/x",
                                                                   "extra": 1}]):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    settings.checked({"web_targets": bad})
