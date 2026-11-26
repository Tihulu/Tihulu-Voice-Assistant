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

    def test_settings_replace_syncs_directory(self):
        from unittest.mock import patch
        with patch.object(settings, "_sync_parent", wraps=settings._sync_parent) as sync:
            settings.save(self.new, self.path)
        sync.assert_called_once_with(self.path)
        self.assertEqual(settings.load(self.path), self.new)

    def test_uncertain_choice_sync_failure_never_runs_registry_commit(self):
        from unittest.mock import patch
        settings.save(self.old, self.path)
        with patch.object(settings, "_sync_parent", side_effect=OSError("fixture")):
            with self.assertRaisesRegex(Hold, "settings_recovery_required"):
                with settings.choice_transaction(self.new, self.path, expected=self.old):
                    self.fail("unexpected registry commit")
        self.assertEqual(settings.load(self.path), self.new)

    def test_absent_settings_compensation_syncs_directory(self):
        from unittest.mock import patch
        with patch.object(settings, "_sync_parent", wraps=settings._sync_parent) as sync:
            with self.assertRaisesRegex(Hold, "fixture"):
                with settings.choice_transaction(self.new, self.path):
                    raise Hold("fixture")
        self.assertFalse(self.path.exists())
        self.assertEqual(sync.call_count, 2)

    def test_forget_sync_failure_reports_uncertain_removal(self):
        from unittest.mock import patch
        settings.save(self.old, self.path)
        with patch.object(settings, "_sync_parent", side_effect=OSError("fixture")):
            with self.assertRaisesRegex(Hold, "settings_recovery_required"):
                settings.forget(self.path)
        self.assertFalse(self.path.exists())

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


class SettingsSnapshotBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "models.json"

    def test_stale_snapshot_does_not_overwrite_current_preferences(self):
        before = settings.empty()
        newer = settings.empty()
        newer["text_scale"] = "large"
        settings.save(newer, self.path)
        with self.assertRaisesRegex(Hold, "settings_changed"):
            with settings.choice_transaction(before, self.path, expected=before):
                self.fail("unexpected commit")
        self.assertEqual(settings.load(self.path), newer)

    def test_stale_plain_save_and_forget_preserve_newer_settings(self):
        before = settings.empty()
        newer = settings.empty()
        newer["text_scale"] = "large"
        settings.save(newer, self.path)
        for action in (lambda: settings.save(before, self.path, expected=before),
                       lambda: settings.forget(self.path, expected=before)):
            with self.assertRaisesRegex(Hold, "settings_changed"):
                action()
            self.assertEqual(settings.load(self.path), newer)

    def test_current_snapshot_allows_plain_save_and_forget(self):
        before = settings.empty()
        newer = settings.empty()
        newer["text_scale"] = "large"
        settings.save(newer, self.path, expected=before)
        self.assertEqual(settings.load(self.path), newer)
        self.assertTrue(settings.forget(self.path, expected=newer))
        self.assertEqual(settings.load(self.path), before)

    def test_audio_snapshot_refuses_changed_voice_context(self):
        before = settings.empty()
        settings.save(before, self.path)
        for field, value in (("stt", "/fixture/changed.bin"),
                             ("voices", {"en": "/fixture/new.onnx"}),
                             ("profile", "strict"),
                             ("market_refs", {"opaque": "/fixture/new.bin"}),
                             ("resources", {"profile": "performance"})):
            with self.subTest(field=field):
                newer = settings.empty()
                newer[field] = value
                settings.save(newer, self.path)
                with self.assertRaisesRegex(Hold, "audio_settings_changed_restart_required"):
                    settings.require_current_audio_selection(before, self.path)

    def test_unrelated_preference_does_not_revoke_audio_context(self):
        before = settings.empty()
        newer = settings.empty()
        newer["text_scale"] = "large"
        newer["show_spoken_text"] = True
        settings.save(newer, self.path)
        settings.require_current_audio_selection(before, self.path)

    def test_optional_audio_digest_preserves_legacy_serialization(self):
        before = settings.empty()
        self.assertEqual(settings._serialize(before), settings._serialize({**before, "audio_digests": {}}))
        self.assertNotIn("audio_digests", settings.checked(before))

    def test_audio_digest_requires_matching_reference(self):
        digest = "a" * 64
        ref = "sha256-" + digest[:32]
        cfg = {**settings.empty(), "market_refs": {ref: "/fixture/model.bin"},
               "audio_digests": {ref: digest}}
        settings.save(cfg, self.path)
        self.assertEqual(settings.load(self.path), cfg)
        for bad in ({ref: "b" * 64}, {"other": digest}, {ref: digest[:32]}):
            with self.assertRaisesRegex(Hold, "invalid_local_settings"):
                settings.save({**cfg, "audio_digests": bad}, self.path)
        self.assertEqual(settings.load(self.path), cfg)

    def test_voice_digest_requires_registered_full_weight_digest(self):
        digest = "a" * 64
        ref = "sha256-" + digest[:32]
        cfg = {**settings.empty(), "market_refs": {ref: "/fixture/voice.onnx"},
               "audio_digests": {ref: digest}, "voice_digests": {ref: "b" * 64}}
        settings.save(cfg, self.path)
        self.assertEqual(settings.load(self.path), cfg)
        for bad in ({**cfg, "audio_digests": {}},
                    {**cfg, "market_refs": {ref: "/fixture/model.bin"}},
                    {**cfg, "voice_digests": {ref: "invalid"}}):
            with self.assertRaisesRegex(Hold, "invalid_local_settings"):
                settings.save(bad, self.path)
        self.assertEqual(settings.load(self.path), cfg)

    def test_reset_of_already_absent_file_is_idempotent(self):
        before = settings.empty()
        before["stt"] = "/fixture/old.bin"
        self.assertFalse(settings.forget(self.path, expected=before))
        existed, proposed = settings.reset_choices("strict", expected=before, path=self.path)
        self.assertFalse(existed)
        self.assertEqual(proposed, {**settings.empty(), "profile": "strict"})
        self.assertEqual(settings.load(self.path), proposed)

    def test_reset_refuses_present_newer_file(self):
        before = settings.empty()
        newer = {**before, "text_scale": "large"}
        settings.save(newer, self.path)
        with self.assertRaisesRegex(Hold, "settings_changed"):
            settings.reset_choices("strict", expected=before, path=self.path)
        self.assertEqual(settings.load(self.path), newer)

    def test_reset_keeps_lock_through_single_publication(self):
        from unittest.mock import patch
        before = {**settings.empty(), "stt": "/fixture/old.bin"}
        settings.save(before, self.path)
        original = settings._replace_text
        def write(path, text):
            self.assertEqual(settings.load(path), before)
            with self.assertRaisesRegex(Hold, "settings_busy"):
                settings.save(settings.empty(), path)
            original(path, text)
        with patch.object(settings, "_replace_text", side_effect=write) as replace:
            existed, proposed = settings.reset_choices("strict", expected=before, path=self.path)
        self.assertTrue(existed)
        replace.assert_called_once()
        self.assertEqual(settings.load(self.path), proposed)

    def test_current_snapshot_allows_choice(self):
        before = settings.empty()
        settings.save(before, self.path)
        newer = settings.empty()
        newer["stt"] = "/fixture/model.bin"
        with settings.choice_transaction(newer, self.path, expected=before):
            pass
        self.assertEqual(settings.load(self.path), newer)

    def test_duplicate_nested_keys_are_refused(self):
        self.path.write_text('{"voices":{"tr":"first","tr":"second"}}')
        with self.assertRaisesRegex(Hold, "settings_unreadable"):
            settings.load(self.path)

    def test_symlink_and_fifo_are_refused(self):
        import os
        outside = self.path.with_name("outside")
        outside.write_text("{}")
        self.path.symlink_to(outside)
        with self.assertRaises(Hold):
            settings.load(self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(Hold):
            settings.load(self.path)
        self.assertEqual(outside.read_text(), "{}")

    def test_growth_during_read_is_refused(self):
        from unittest.mock import patch
        import os
        self.path.write_text("{}")
        real_read = os.read
        def growing(fd, n):
            self.path.write_bytes(b"x" * 32769)
            return real_read(fd, n)
        with patch.object(settings.os, "read", side_effect=growing):
            with self.assertRaisesRegex(Hold, "settings_changed"):
                settings.load(self.path)

    def test_disappearing_opened_path_is_not_treated_as_empty(self):
        from unittest.mock import patch
        import os
        self.path.write_text("{}")
        real_read = os.read
        removed = False
        def disappearing(fd, n):
            nonlocal removed
            if not removed:
                self.path.unlink()
                removed = True
            return real_read(fd, n)
        with patch.object(settings.os, "read", side_effect=disappearing):
            with self.assertRaisesRegex(Hold, "settings_changed"):
                settings.load(self.path)


class JanChoiceJournalTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        from tihulu_jarvis import profiles
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.path = root / "models.json"
        self.store = profiles.Store(root / "profiles")
        self.before = self.store.save(profiles.Profile(id="local-only", name="Fixture",
                                                      version=1, llm_model_id="old"))
        self.after = replace(self.before, version=2, llm_model_id="new").checked()
        self.old = {**settings.empty(), "profile": self.before.id, "llm": "old"}
        self.new = {**self.old, "llm": "new"}
        settings.save(self.old, self.path)

    def stage(self):
        record = settings._jan_record(self.store, self.before, self.after, self.old, self.new)
        import json
        settings._replace_text(settings._choice_journal(self.path), json.dumps(record, sort_keys=True))

    def test_success_removes_journal_after_profile_commit(self):
        with self.store._mutation_lock():
            with settings.choice_transaction(self.new, self.path, expected=self.old,
                    jan_transition=(self.store, self.before, self.after)):
                self.assertTrue(settings.choice_pending(self.path))
                self.store._save_locked(self.after)
        self.assertFalse(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.new)

    def test_compensated_failure_clears_only_old_profile_journal(self):
        with self.store._mutation_lock():
            with self.assertRaisesRegex(Hold, "fixture"):
                with settings.choice_transaction(self.new, self.path, expected=self.old,
                        jan_transition=(self.store, self.before, self.after)):
                    raise Hold("fixture")
        self.assertFalse(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.old)

    def test_interrupted_settings_stage_recovers_old_choice(self):
        self.stage()
        settings._replace_text(self.path, settings._serialize(self.new))
        self.assertTrue(settings.recover_jan_choice(self.store, self.path))
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(self.store.load(self.before.id), self.before)
        self.assertFalse(settings.choice_pending(self.path))

    def test_committed_profile_and_settings_finalize_missing_history(self):
        self.stage()
        settings._replace_text(self.path, settings._serialize(self.new))
        self.store.save(self.after)
        (self.store.history / "local-only-v2.json").unlink()
        self.assertTrue(settings.recover_jan_choice(self.store, self.path))
        self.assertEqual(self.store.versions(self.before.id)[-1], self.after)
        self.assertEqual(settings.load(self.path), self.new)
        self.assertFalse(settings.choice_pending(self.path))

    def test_committed_profile_with_compensated_settings_is_held(self):
        self.stage()
        self.store.save(self.after)
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.recover_jan_choice(self.store, self.path)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(self.store.load(self.before.id), self.after)

    def test_unrelated_settings_and_profile_are_never_rewritten(self):
        self.stage()
        changed = {**self.old, "text_scale": "large"}
        settings._replace_text(self.path, settings._serialize(changed))
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.recover_jan_choice(self.store, self.path)
        self.assertEqual(settings.load(self.path), changed)
        self.assertTrue(settings.choice_pending(self.path))

    def test_pending_journal_blocks_ordinary_writes(self):
        self.stage()
        for action in (lambda: settings.save(self.new, self.path),
                       lambda: settings.forget(self.path)):
            with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
                action()
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            with settings.choice_transaction(self.new, self.path):
                self.fail("unexpected write")

    def test_journal_refuses_privilege_change(self):
        from dataclasses import replace
        with self.assertRaisesRegex(Hold, "invalid_jan_choice_transition"):
            with settings.choice_transaction(self.new, self.path, expected=self.old,
                    jan_transition=(self.store, self.before, replace(self.after, name="changed"))):
                self.fail("unexpected commit")
        self.assertFalse(settings.choice_pending(self.path))

    def interrupt_child(self, phase):
        import subprocess
        import sys
        code = """
import os, sys
from pathlib import Path
from dataclasses import replace
sys.path.insert(0, sys.argv[1])
from tihulu_jarvis import settings, profiles
path, phase = Path(sys.argv[2]), sys.argv[3]
store = profiles.Store(path.parent / "profiles")
before = store.load("local-only")
after = replace(before, version=2, llm_model_id="new").checked()
old = settings.load(path)
new = {**old, "llm": "new"}
if phase == "journal":
    original = settings._replace_text
    def stop(p, text):
        original(p, text)
        if p == settings._choice_journal(path):
            os._exit(73)
    settings._replace_text = stop
with store._mutation_lock():
    with settings.choice_transaction(new, path, expected=old,
            jan_transition=(store, before, after)):
        if phase == "settings":
            os._exit(73)
        if phase == "active":
            original = store._write
            def stop(p, text):
                original(p, text)
                if p == store._path(before.id):
                    os._exit(73)
            store._write = stop
        store._save_locked(after)
        if phase == "committed":
            os._exit(73)
sys.exit(99)
"""
        child = subprocess.run([sys.executable, "-c", code,
            str(Path(settings.__file__).resolve().parent.parent), str(self.path), phase],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertTrue(settings.choice_pending(self.path))

    def test_real_process_death_after_journal(self):
        self.interrupt_child("journal")
        self.assertTrue(settings.recover_jan_choice(self.store, self.path))
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(self.store.load(self.before.id), self.before)

    def test_real_process_death_after_settings_publication(self):
        self.interrupt_child("settings")
        self.assertEqual(settings.load(self.path), self.new)
        self.assertTrue(settings.recover_jan_choice(self.store, self.path))
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(self.store.load(self.before.id), self.before)

    def test_real_process_death_after_active_profile_before_history(self):
        self.interrupt_child("active")
        self.assertFalse((self.store.history / "local-only-v2.json").exists())
        self.assertTrue(settings.recover_jan_choice(self.store, self.path))
        self.assertEqual(settings.load(self.path), self.new)
        self.assertEqual(self.store.versions(self.before.id)[-1], self.after)

    def test_real_process_death_after_profile_and_history(self):
        self.interrupt_child("committed")
        self.assertTrue(settings.recover_jan_choice(self.store, self.path))
        self.assertFalse(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.new)
        self.assertEqual(self.store.load(self.before.id), self.after)

    def test_recovery_preserves_replaced_identical_journal(self):
        from unittest.mock import patch
        self.stage()
        settings._replace_text(self.path, settings._serialize(self.new))
        original = settings._replace_text
        journal = settings._choice_journal(self.path)
        def replaced(path, text):
            original(path, text)
            if path == self.path:
                original(journal, journal.read_text())
        with patch.object(settings, "_replace_text", side_effect=replaced):
            with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
                settings.recover_jan_choice(self.store, self.path)
        self.assertTrue(journal.exists())
        self.assertEqual(settings.load(self.path), self.old)

    def committed_review_fixture(self):
        from tihulu_jarvis import market
        registry = market.Registry(self.path.parent / "market")
        card = registry.save_card(market.register_installed(market.ModelCard(
            model_id="new", display_name="New Jan model", category="llm",
            provider="jan", engine="jan", installed=True)))
        self.stage()
        self.store.save(self.after)
        return registry, card

    def test_explicit_committed_review_repairs_only_local_preference(self):
        registry, card = self.committed_review_fixture()
        profile = self.store.snapshot(self.before.id)
        review = settings.review_committed_jan_choice(self.store, registry, self.path)
        self.assertEqual(review[0], card)
        self.assertEqual(settings.load(self.path), self.old)
        self.assertEqual(settings.resolve_committed_jan_choice(
            self.store, registry, expected=review, path=self.path), self.new)
        self.assertEqual(self.store.snapshot(self.before.id), profile)
        self.assertEqual(registry.card(card.model_id), card)
        self.assertFalse(settings.choice_pending(self.path))

    def test_explicit_resolution_refuses_changed_registry_card(self):
        from dataclasses import replace
        registry, card = self.committed_review_fixture()
        review = settings.review_committed_jan_choice(self.store, registry, self.path)
        registry.save_card(replace(card, display_name="Changed"), expected=card)
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.resolve_committed_jan_choice(self.store, registry,
                                                  expected=review, path=self.path)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.old)

    def test_explicit_resolution_refuses_changed_profile(self):
        from dataclasses import replace
        registry, card = self.committed_review_fixture()
        review = settings.review_committed_jan_choice(self.store, registry, self.path)
        self.store.save(replace(self.after, version=3, name="Changed"))
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.resolve_committed_jan_choice(self.store, registry,
                                                  expected=review, path=self.path)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.old)

    def test_explicit_review_refuses_unregistered_jan_card(self):
        from tihulu_jarvis import market
        registry = market.Registry(self.path.parent / "market")
        registry.save_card(market.ModelCard(model_id="new", display_name="New",
                    category="llm", provider="jan", engine="jan", installed=True))
        self.stage()
        self.store.save(self.after)
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.review_committed_jan_choice(self.store, registry, self.path)
        self.assertEqual(settings.load(self.path), self.old)

    def test_committed_recovery_refuses_settings_replaced_during_history_repair(self):
        from unittest.mock import patch
        self.stage()
        settings._replace_text(self.path, settings._serialize(self.new))
        self.store.save(self.after)
        original = self.store._ensure_current_archive_locked
        def changed(profile):
            original(profile)
            settings._replace_text(self.path, settings._serialize(self.new))
        with patch.object(self.store, "_ensure_current_archive_locked", side_effect=changed):
            with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
                settings.recover_jan_choice(self.store, self.path)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.new)

    def test_review_refuses_replaced_identical_journal_during_card_read(self):
        from unittest.mock import patch
        registry, card = self.committed_review_fixture()
        original = registry.card
        journal = settings._choice_journal(self.path)
        def changed(model_id):
            result = original(model_id)
            settings._replace_text(journal, journal.read_text())
            return result
        with patch.object(registry, "card", side_effect=changed):
            with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
                settings.review_committed_jan_choice(self.store, registry, self.path)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.old)

    def test_jan_automatic_recovery_preserves_simultaneous_audio_journal(self):
        self.stage()
        settings._replace_text(self.path, settings._serialize(self.new))
        audio = self.path.with_name(self.path.name + ".audio-choice-journal")
        audio.write_text("{}")
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.recover_jan_choice(self.store, self.path)
        self.assertEqual(settings.load(self.path), self.new)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(audio.read_text(), "{}")

    def test_jan_confirmation_refuses_audio_journal_created_after_review(self):
        registry, card = self.committed_review_fixture()
        review = settings.review_committed_jan_choice(self.store, registry, self.path)
        audio = self.path.with_name(self.path.name + ".audio-choice-journal")
        audio.write_text("{}")
        with self.assertRaisesRegex(Hold, "llm_choice_recovery_required"):
            settings.resolve_committed_jan_choice(self.store, registry,
                                                  expected=review, path=self.path)
        self.assertTrue(settings.choice_pending(self.path))
        self.assertEqual(settings.load(self.path), self.old)

    def test_jan_review_never_runs_pending_registry_recovery(self):
        registry, card = self.committed_review_fixture()
        registry.publication_journal.write_text("{}")
        with self.assertRaisesRegex(Hold, "registry_recovery_required"):
            settings.review_committed_jan_choice(self.store, registry, self.path)
        self.assertEqual(registry.publication_journal.read_text(), "{}")
        self.assertTrue(settings.choice_pending(self.path))
