# SPDX-License-Identifier: AGPL-3.0-only
"""Run only in the disposable GTK/Xvfb workflow."""
import unittest
from dataclasses import replace
import test_voice_composer_handoff as fixtures
from voice_draft_gtk import Gtk,VoiceDraftWidget

class RealGtkDraftTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.VoiceComposerHandoffTests()
        f.setUp()
        self.context,self.args,self.jobs=f.ctx,f.args,f.jobs
        self.widget=VoiceDraftWidget(lambda:self.context)

    def test_multiline_roundtrip_no_session_mutation(self):
        button=self.widget.present(*self.args)
        self.assertIsInstance(button,Gtk.Button)
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"Merhaba\nHello\nGuten Tag")
        self.assertEqual(self.widget.status.get_text(),"Draft ready; not sent")
        self.assertEqual(self.context.session.history,())
        self.assertEqual(self.context.session.epoch,0)
        self.assertIsNone(self.context.session.active)

    def test_typing_then_clearing_invalidates_previous_button(self):
        old=self.widget.present(*self.args)
        self.widget.composer.get_buffer().set_text("user draft")
        self.widget.composer.get_buffer().set_text("")
        old.emit("clicked")
        self.assertEqual(self.widget.text(),"")

    def test_existing_text_never_overwritten(self):
        self.widget.composer.get_buffer().set_text("Keep this")
        self.assertIsNone(self.widget.present(*self.args))
        self.assertEqual(self.widget.text(),"Keep this")

    def test_replacement_disconnects_old_button(self):
        old=self.widget.present(*self.args)
        current=self.widget.present(*self.args)
        old.emit("clicked")
        self.assertEqual(self.widget.text(),"")
        current.emit("clicked")
        self.assertEqual(self.widget.text(),"Merhaba\nHello\nGuten Tag")

    def test_changed_profile_or_revoked_receipt_returns_hold(self):
        button=self.widget.present(*self.args)
        self.context=replace(self.context,generation="new")
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"")
        self.assertTrue(self.widget.status.get_text().startswith("HOLD:"))
        button=self.widget.present(*self.args)
        self.jobs[1].cancel()
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"")
        self.assertTrue(self.widget.status.get_text().startswith("HOLD:"))

    def test_cancel_clears_review_and_disconnects_button(self):
        button=self.widget.present(*self.args)
        self.context.session.invalidate()
        self.widget.invalidate()
        button.emit("clicked")
        b=self.widget.preview.get_buffer()
        self.assertEqual(b.get_text(b.get_start_iter(),b.get_end_iter(),True),"")
        self.assertEqual(self.widget.text(),"")

if __name__=="__main__":
    Gtk.init()
    unittest.main(verbosity=2)
