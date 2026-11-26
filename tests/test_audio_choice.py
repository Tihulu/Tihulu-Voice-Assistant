# SPDX-License-Identifier: AGPL-3.0-only
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tihulu_jarvis import audio_choice, market, settings
from tihulu_jarvis.voice.m1 import Hold


class AudioChoiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.path = root / "models.json"
        self.registry = market.Registry(root / "market")
        self.card = self.registry.save_card(market.register_installed(market.ModelCard(
            model_id="fixture-stt", display_name="Fixture", category="stt",
            engine="whisper.cpp", installed=True, storage_ref="fixture-ref")))
        self.active = market.advance(market.advance(self.card, "TESTABLE"), "ACTIVE")
        self.old = {**settings.empty(), "market_refs": {"fixture-ref": "/fixture/new.bin"},
                    "stt": "/fixture/old.bin"}
        self.new = {**self.old, "stt": "/fixture/new.bin"}
        settings.save(self.old, self.path)

    def commit(self):
        return audio_choice.commit(self.registry, self.card, self.active,
                                   self.old, self.new, "stt", self.path)

    def test_success_commits_both(self):
        saved = self.commit()
        self.assertEqual(saved.state, "ACTIVE")
        self.assertEqual(settings.load(self.path), self.new)
        self.assertFalse(settings.audio_choice_pending(self.path))

    def test_registry_failure_compensates_old_choice(self):
        with patch.object(self.registry, "save_card", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.commit()
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(self.registry.card(self.card.model_id), self.card)
        self.assertFalse(settings.audio_choice_pending(self.path))

    def test_uncertain_registry_commit_recovers_new_choice(self):
        original = self.registry.save_card
        def changed(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("fixture")
        with patch.object(self.registry, "save_card", side_effect=changed):
            with self.assertRaisesRegex(Hold, audio_choice.REASON):
                self.commit()
        self.assertTrue(settings.audio_choice_pending(self.path))
        self.assertTrue(audio_choice.recover(self.registry, self.path))
        self.assertEqual(settings.load(self.path), self.new)
        self.assertEqual(self.registry.card(self.card.model_id).state, "ACTIVE")

    def test_uncertain_settings_stage_recovers_old_choice(self):
        original = settings._replace_text
        def changed(p, text):
            original(p, text)
            if p == self.path:
                raise Hold("settings_recovery_required")
        with patch.object(settings, "_replace_text", side_effect=changed):
            with self.assertRaisesRegex(Hold, "settings_recovery_required"):
                self.commit()
        self.assertTrue(settings.audio_choice_pending(self.path))
        self.assertTrue(audio_choice.recover(self.registry, self.path))
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(self.registry.card(self.card.model_id), self.card)

    def test_pending_journal_blocks_other_settings_writes(self):
        with patch.object(self.registry, "save_card", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.commit()
        with self.assertRaisesRegex(Hold, audio_choice.REASON):
            settings.save(self.old, self.path)
        self.assertTrue(audio_choice.recover(self.registry, self.path))

    def test_unrelated_registry_generation_preserved(self):
        with patch.object(self.registry, "save_card", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.commit()
        self.registry.save_card(market.ModelCard(
            model_id="other", display_name="Other", category="llm"))
        with self.assertRaisesRegex(Hold, audio_choice.REASON):
            audio_choice.recover(self.registry, self.path)
        self.assertTrue(settings.audio_choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.new)
