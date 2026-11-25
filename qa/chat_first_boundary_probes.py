# SPDX-License-Identifier: AGPL-3.0-only
"""Maintainer probes against frozen #115. No real accounts, audio or network."""
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from dataclasses import replace

import test_desktop as fixture
from tihulu_jarvis import desktop, profiles


class SuccessorBoundaryProbes(fixture.DesktopFixture):
    def setUp(self):
        super().setUp()
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "notes.txt"
        self.path.write_text("SYNTHETIC-PRIVATE-CANARY", encoding="utf-8")

    def reader(self, expires=0):
        profile = profiles.Profile(id="probe-reader", name="Probe reader",
            based_on_mode="LOCAL_ONLY",
            filesystem_rules=tuple(profiles.Rule("filesystem_read", (scope,),
                "allow", "fixture" if expires else "", expires)
                for scope in ("folder", "file")))
        self.ui.store.save(profile)
        self.ui.activate_profile(profile.id)
        return profile

    def legacy_preview(self):
        self.assertIsNotNone(self.ui.list_folder(str(self.tmp)))
        self.ui.folder_entries.select_row(self.ui.folder_entries.get_first_child())
        self.assertEqual(self.ui.read_selected_file(), "SYNTHETIC-PRIVATE-CANARY")

    def test_legacy_preview_cleared_on_end_conversation(self):
        self.reader()
        self.legacy_preview()
        self.ui.interrupt(True)
        self.assertEqual(self.ui.file_preview.get_text(), "")
        self.assertIsNone(self.ui.research_folder)

    def test_legacy_preview_cleared_on_profile_generation_change(self):
        profile = self.reader()
        self.legacy_preview()
        self.ui.store.save(replace(profile, version=profile.version + 1,
            filesystem_rules=tuple(profiles.Rule("filesystem_read", (scope,), "deny")
                for scope in ("folder", "file"))))
        self.ui.refresh()
        self.assertEqual(self.ui.file_preview.get_text(), "")
        self.assertIsNone(self.ui.research_folder)

    def test_chat_preview_cleared_on_file_grant_expiry(self):
        clock = [time.time()]
        self.reader(clock[0] + 60)
        view = self.ui.chat_files
        with patch.object(profiles.time, "time", side_effect=lambda: clock[0]):
            view.start(str(self.tmp))
            self.until(lambda: not self.ui.busy)
            view.entries.set_active_id("0")
            self.click(view.read_button)
            self.until(lambda: not self.ui.busy)
            self.assertEqual(view.preview.get_text(), "SYNTHETIC-PRIVATE-CANARY")
            clock[0] += 120
            self.ui.refresh()
            self.assertEqual(view.preview.get_text(), "")
            self.assertFalse(view.panel.get_visible())
        self.jan.assert_not_called()

    def test_chat_worker_revocation_before_publication(self):
        profile = self.reader()
        view = self.ui.chat_files
        entered, release = Event(), Event()
        original = desktop.files.list_dir
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            if not release.wait(10):
                raise AssertionError("worker fixture timeout")
            return result
        with patch.object(desktop.files, "list_dir", side_effect=delayed):
            view.start(str(self.tmp))
            try:
                self.until(entered.is_set)
                self.ui.store.save(replace(profile, version=profile.version + 1,
                    filesystem_rules=tuple(profiles.Rule("filesystem_read", (scope,), "deny")
                        for scope in ("folder", "file"))))
            finally:
                release.set()
            self.until(lambda: not self.ui.busy)
        self.assertFalse(view.panel.get_visible())
        self.assertIsNone(view.folder)
        self.assertEqual(view.listing.get_text(), "")
        self.assertEqual(self.ui.board.rows[-1].state, "failed")
        self.jan.assert_not_called()

    def test_model_inventory_cleared_when_folder_grant_expires(self):
        clock = [time.time()]
        self.reader(clock[0] + 60)
        (self.tmp / "synthetic.bin").write_bytes(b"fixture")
        with patch.object(profiles.time, "time", side_effect=lambda: clock[0]):
            self.ui.scan_model_folder(str(self.tmp))
            self.until(lambda: not self.ui.busy)
            self.assertIsNotNone(self.ui.model_scan_result)
            clock[0] += 120
            self.ui.refresh()
            self.assertIsNone(self.ui.model_scan_result)
            self.assertFalse(self.ui.model_candidate_choose.get_sensitive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
