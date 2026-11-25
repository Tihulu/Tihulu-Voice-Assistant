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


class ForgetTests(unittest.TestCase):
    def test_forgetting_removes_only_the_choices_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            settings.save(settings.empty(), path)
            self.assertTrue(settings.forget(path))
            self.assertFalse(path.exists())
            self.assertFalse(settings.forget(path))
            # A missing file is not an error, and nothing else is touched.
            self.assertTrue(settings.load(path)["text_scale"] == "normal")


class SourceSettingsTests(unittest.TestCase):
    def test_a_stored_source_is_revalidated_on_read(self):
        entry = {"host": "huggingface.co", "kind": "hf", "filter": "whisper"}
        self.assertEqual(settings.checked({"sources": [entry]})["sources"], [entry])
        for bad in ([{"host": "localhost", "kind": "hf"}], [{"host": "huggingface.co",
                                                             "kind": "magnet"}],
                    ["huggingface.co"], [{"host": "huggingface.co", "kind": "hf",
                                          "filter": "x" * 97}], None, {}):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    settings.checked({"sources": bad})


class ChoiceTransactionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "models.json"
        self.old = settings.empty()
        self.new = settings.empty()
        self.new["stt"] = "/fixture/model.bin"

    def test_success_retains_new_settings(self):
        settings.save(self.old, self.path)
        with settings.choice_transaction(self.new, self.path):
            self.assertEqual(settings.load(self.path), self.new)
        self.assertEqual(settings.load(self.path), self.new)

    def test_failure_restores_exact_previous_text(self):
        import json
        original = json.dumps(self.old, indent=2)
        self.path.write_text(original)
        with self.assertRaisesRegex(Hold, "registry_fixture"):
            with settings.choice_transaction(self.new, self.path):
                raise Hold("registry_fixture")
        self.assertEqual(self.path.read_text(), original)

    def test_failure_restores_absence(self):
        with self.assertRaisesRegex(Hold, "registry_fixture"):
            with settings.choice_transaction(self.new, self.path):
                raise Hold("registry_fixture")
        self.assertFalse(self.path.exists())

    def test_interleaving_writers_and_forget_refused(self):
        settings.save(self.old, self.path)
        with settings.choice_transaction(self.new, self.path):
            with self.assertRaisesRegex(Hold, "settings_busy"):
                settings.save(self.old, self.path)
            with self.assertRaisesRegex(Hold, "settings_busy"):
                settings.forget(self.path)
        self.assertEqual(settings.load(self.path), self.new)

    def test_compensation_failure_reports_recovery_required(self):
        from unittest.mock import patch
        settings.save(self.old, self.path)
        original = settings._replace_text
        def fail_restore(path, text):
            if settings._serialize(self.old) == text:
                raise OSError("fixture")
            original(path, text)
        with patch.object(settings, "_replace_text", side_effect=fail_restore):
            with self.assertRaisesRegex(Hold, "settings_recovery_required"):
                with settings.choice_transaction(self.new, self.path):
                    raise Hold("registry_fixture")
        self.assertEqual(settings.load(self.path), self.new)

    def test_oversized_settings_refused_without_overwriting(self):
        settings.save(self.old, self.path)
        huge = settings.empty()
        huge["market_refs"] = {str(i): "x" * 4096 for i in range(10)}
        with self.assertRaisesRegex(Hold, "invalid_local_settings"):
            settings.save(huge, self.path)
        self.assertEqual(settings.load(self.path), self.old)
