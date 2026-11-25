# SPDX-License-Identifier: AGPL-3.0-only
"""Maintainer negative probes of the frozen chat-first candidate."""
import unittest
import time
from pathlib import Path
from unittest.mock import patch
import test_desktop as f
from tihulu_jarvis import profiles

class ComplaintSweep(f.DesktopFixture):
    def attachment(self, expiry=None):
        if expiry is not None:
            p=profiles.Profile(id="sweep-reader",name="Sweep reader",
                filesystem_rules=(profiles.Rule("filesystem_read",("attachment",),"allow","fixture",expiry),))
            self.ui.store.save(p)
            self.ui.activate_profile(p.id)
        self.connect_model()
        path=Path(self.profile_home).resolve()/"fixture.txt"
        path.write_text("SYNTHETIC-ATTACHMENT",encoding="utf-8")
        self.assertIsNotNone(self.ui.read_attachment(str(path)))

    def test_idle_attachment_keeps_hard_stop_available(self):
        self.attachment()
        self.ui.refresh()
        self.assertTrue(self.ui.quick_stop.get_sensitive())
        self.click(self.ui.quick_stop)
        self.assertIsNone(self.ui.attachment)
        self.assertFalse(self.ui.quick_stop.get_sensitive())

    def test_idle_attachment_expiry_clears_retained_file(self):
        clock=[time.time()]
        with patch.object(profiles.time,"time",side_effect=lambda:clock[0]):
            self.attachment(clock[0]+60)
            clock[0]+=120
            self.ui.refresh()
            self.assertIsNone(self.ui.attachment)
            self.assertEqual(self.ui.attachment_note.get_text(),"")

    def test_empty_search_drops_cached_matches(self):
        self.connect_model()
        self.text("SYNTHETIC-SEARCH-CANARY")
        self.ui.search_panel.set_expanded(True)
        self.ui.search_entry.set_text("SYNTHETIC-SEARCH-CANARY")
        self.assertTrue(self.ui.search_matches)
        self.ui.clear_search()
        self.assertEqual(self.ui.search_matches,())
        self.assertIsNone(self.ui.search_results.get_first_child())

    def test_end_closed_search_panel_drops_retained_snippets(self):
        self.connect_model()
        self.text("SYNTHETIC-SEARCH-CANARY")
        self.ui.search_panel.set_expanded(True)
        self.ui.search_entry.set_text("SYNTHETIC-SEARCH-CANARY")
        self.assertTrue(self.ui.search_matches)
        self.ui.search_panel.set_expanded(False)
        self.ui.interrupt(True)
        self.assertEqual(self.ui.session.history,())
        self.assertEqual(self.ui.search_matches,())
        self.assertIsNone(self.ui.search_results.get_first_child())
        self.assertNotIn("CANARY",self.ui.search_note.get_text())

if __name__=="__main__":
    unittest.main(verbosity=2)
