# SPDX-License-Identifier: AGPL-3.0-only
"""Run only in the disposable GTK/Xvfb workflow."""
import unittest
from dataclasses import replace
import test_voice_composer_handoff as fixtures
from voice_draft_gtk import Gtk,GLib,VoiceDraftWidget

class RealGtkDraftTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.VoiceComposerHandoffTests()
        f.setUp()
        self.context,self.args,self.jobs=f.ctx,f.args,f.jobs
        self.widget=VoiceDraftWidget(lambda:self.context,refresh_ms=10)
        self.addCleanup(self.widget.close)

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

    def test_refresh_clears_invalid_review_before_user_click(self):
        button=self.widget.present(*self.args)
        self.assertTrue(self.widget.refresh_review())
        self.jobs[0].cancel()
        self.assertFalse(self.widget.refresh_review())
        b=self.widget.preview.get_buffer()
        self.assertEqual(b.get_text(b.get_start_iter(),b.get_end_iter(),True),"")
        self.assertTrue(self.widget.status.get_text().startswith("HOLD:"))
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"")

    def test_refresh_exact_expiry_removes_display(self):
        from unittest.mock import patch
        fixture=fixtures.VoiceComposerHandoffTests()
        with patch("tihulu_jarvis.profiles.time.time",return_value=1000), \
             patch("tihulu_jarvis.profiles.time.monotonic",return_value=200):
            fixture.setUp()
            self.context,self.args=fixture.ctx,fixture.args
            button=self.widget.present(*self.args)
            self.assertTrue(self.widget.refresh_review())
        with patch("tihulu_jarvis.profiles.time.time",return_value=1060), \
             patch("tihulu_jarvis.profiles.time.monotonic",return_value=260):
            self.assertFalse(self.widget.refresh_review())
            self.assertIn("expired",self.widget.status.get_text())
            button.emit("clicked")
            self.assertEqual(self.widget.text(),"")
            b=self.widget.preview.get_buffer()
            self.assertEqual(b.get_text(b.get_start_iter(),b.get_end_iter(),True),"")

    def _pump_until(self,predicate):
        import time
        deadline=time.monotonic()+2
        context=GLib.MainContext.default()
        while not predicate() and time.monotonic()<deadline:
            if context.pending():
                context.iteration(False)
            else:
                time.sleep(.002)
        self.assertTrue(predicate())

    def test_owned_tick_clears_revoked_preview_without_explicit_refresh(self):
        button=self.widget.present(*self.args)
        self.assertIsNotNone(self.widget._tick)
        self.jobs[0].cancel()
        self._pump_until(lambda:self.widget._tick is None)
        self.assertTrue(self.widget.status.get_text().startswith("HOLD:"))
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"")

    def test_close_removes_source_and_disconnects_old_button(self):
        button=self.widget.present(*self.args)
        self.widget.close()
        self.widget.close()
        self.assertIsNone(self.widget._tick)
        self.assertIsNone(self.widget._review)
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"")
        self.assertIsNone(self.widget.present(*self.args))
        self.assertEqual(self.widget.status.get_text(),"Closed")

    def test_confirmation_removes_tick_and_close_preserves_confirmed_draft(self):
        button=self.widget.present(*self.args)
        button.emit("clicked")
        self.assertIsNone(self.widget._tick)
        expected=self.widget.text()
        self.widget.close()
        self.assertEqual(self.widget.text(),expected)

    def test_context_reader_failure_clears_preview_without_raw_exception(self):
        button=self.widget.present(*self.args)
        def fail():
            raise RuntimeError("private diagnostic")
        self.widget._context_reader=fail
        self._pump_until(lambda:self.widget._tick is None)
        self.assertEqual(self.widget.status.get_text(),"HOLD: draft_context_unavailable")
        button.emit("clicked")
        self.assertEqual(self.widget.text(),"")

    def test_replacement_has_single_owned_source_and_old_button_is_inert(self):
        first=self.widget.present(*self.args)
        first_token=self.widget._tick_token
        second=self.widget.present(*self.args)
        self.assertIsNot(first_token,self.widget._tick_token)
        first.emit("clicked")
        self.assertEqual(self.widget.text(),"")
        second.emit("clicked")
        self.assertIsNone(self.widget._tick)
        self.assertTrue(self.widget.text())

if __name__=="__main__":
    Gtk.init()
    unittest.main(verbosity=2)
