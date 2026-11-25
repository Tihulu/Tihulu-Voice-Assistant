# SPDX-License-Identifier: AGPL-3.0-only
"""Additional maintainer evidence; no independent-review claim."""
import time
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import test_desktop as fixture
from tihulu_jarvis import desktop, profiles


class LifetimeMatrix(fixture.DesktopFixture):
    def setUp(self):
        super().setUp()
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "notes.txt"
        self.path.write_text("SYNTHETIC-SEARCH-CANARY", encoding="utf-8")
        self.clock = [time.time()]

    def grant(self, *, folder=0, file=0):
        p = profiles.Profile(id="matrix-reader", name="Matrix reader",
            based_on_mode="LOCAL_ONLY", filesystem_rules=tuple(
                profiles.Rule("filesystem_read", (scope,), "allow",
                    "fixture" if expiry else "", expiry)
                for scope, expiry in (("folder", folder), ("file", file))))
        self.ui.store.save(p)
        self.ui.activate_profile(p.id)

    def chat_listing(self):
        view = self.ui.chat_files
        view.start(str(self.tmp))
        self.until(lambda: not self.ui.busy)
        self.assert_no_failed_turn()
        self.assertIsNotNone(view.folder)
        view.entries.set_active_id("0")
        return view

    def legacy_listing(self):
        self.assertIsNotNone(self.ui.list_folder(str(self.tmp)))
        self.ui.folder_entries.select_row(self.ui.folder_entries.get_first_child())
        self.assertEqual(self.ui.read_selected_file(), "SYNTHETIC-SEARCH-CANARY")

    def test_file_only_expiry_keeps_folder_but_clears_both_previews(self):
        self.grant(file=self.clock[0] + 60)
        with patch.object(profiles.time, "time", side_effect=lambda: self.clock[0]):
            self.legacy_listing()
            view = self.chat_listing()
            self.click(view.read_button)
            self.until(lambda: not self.ui.busy)
            self.assertIn("CANARY", view.preview.get_text())
            self.clock[0] += 120
            self.until(lambda: not view.preview.get_text() and not self.ui.file_preview.get_text())
            self.assertIsNotNone(view.folder)
            self.assertIsNotNone(self.ui.research_folder)
            self.assertFalse(view.read_button.get_sensitive())
            self.assertFalse(self.ui.file_read.get_sensitive())
            self.assertTrue(self.ui.quick_stop.get_sensitive())
        self.jan.assert_not_called()

    def test_file_only_expiry_clears_completed_search(self):
        self.grant(file=self.clock[0] + 60)
        with patch.object(profiles.time, "time", side_effect=lambda: self.clock[0]):
            view = self.chat_listing()
            view.query.set_text("search")
            self.click(view.search_button)
            self.until(lambda: not self.ui.busy)
            self.assertIn("CANARY", view.search_result.get_text())
            self.clock[0] += 120
            self.until(lambda: not view.search_result.get_text())
            self.assertIsNotNone(view.folder)
            self.assertFalse(view.search_button.get_sensitive())
            self.assertFalse(view.query.get_sensitive())
        self.assertEqual(self.ui.session.history, ())
        self.jan.assert_not_called()

    def test_search_expiry_at_worker_publication(self):
        self.grant(file=self.clock[0] + 60)
        entered, release = Event(), Event()
        real = desktop.files.search_text
        def delayed(*args, **kwargs):
            result = real(*args, **kwargs)
            entered.set()
            if not release.wait(10):
                raise AssertionError("fixture release timed out")
            return result
        with patch.object(profiles.time, "time", side_effect=lambda: self.clock[0]), \
                patch.object(desktop.files, "search_text", side_effect=delayed):
            view = self.chat_listing()
            view.query.set_text("search")
            self.click(view.search_button)
            try:
                self.until(entered.is_set)
                self.clock[0] += 120
            finally:
                release.set()
            self.until(lambda: not self.ui.busy)
            self.assertEqual(view.search_result.get_text(), "")
            self.assertEqual(self.ui.board.rows[-1].state, "failed")
        self.jan.assert_not_called()

    def test_result_timer_single_source_and_hard_stop(self):
        self.grant()
        self.legacy_listing()
        source = self.ui.result_watch
        self.assertIsNotNone(source)
        view = self.chat_listing()
        for _ in range(5):
            self.ui.refresh()
            self.assertEqual(self.ui.result_watch, source)
        self.ui.hard_stop()
        self.until(lambda: self.ui.result_watch is None)
        self.assertIsNone(self.ui.research_folder)
        self.assertIsNone(view.folder)
        self.assertFalse(self.ui.quick_stop.get_sensitive())
        self.assertIsNone(fixture.GLib.MainContext.default().find_source_by_id(source))

    def test_window_close_removes_active_result_timer(self):
        self.grant()
        self.legacy_listing()
        source = self.ui.result_watch
        self.assertIsNotNone(source)
        view = self.chat_listing()
        self.ui.win.close()
        self.until(lambda: not self.app.get_windows())
        self.assertIsNone(self.ui.result_watch)
        self.assertIsNone(fixture.GLib.MainContext.default().find_source_by_id(source))
        self.assertIsNone(self.ui.research_folder)
        self.assertIsNone(view.folder)
        self.ui = None

    def test_legacy_publication_refused_if_grant_expires_during_save(self):
        self.grant(folder=self.clock[0] + 60)
        real = self.ui.save
        def delayed_save():
            real()
            self.clock[0] += 120
        with patch.object(profiles.time, "time", side_effect=lambda: self.clock[0]), \
                patch.object(self.ui, "save", side_effect=delayed_save):
            self.assertIsNone(self.ui.list_folder(str(self.tmp)))
            self.assertIsNone(self.ui.research_folder)
            self.assertIsNone(self.ui.folder_entries.get_first_child())
            self.assertIsNone(self.ui.result_watch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
