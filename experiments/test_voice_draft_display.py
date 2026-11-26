# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from threading import Thread
from gateway import Refused
import test_voice_composer_handoff as fixtures
from voice_draft_display import DraftDisplay,Display

class DraftDisplayTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.VoiceComposerHandoffTests()
        f.setUp()
        self.ctx,self.args,self.jobs=f.ctx,f.args,f.jobs
        self.control=DraftDisplay()

    def show(self):
        return self.control.show(self.ctx,*self.args)

    def accept(self,d,context=None,confirmation=None):
        return self.control.accept(self.ctx if context is None else context,d,
            d.preview.confirmation_digest if confirmation is None else confirmation,*self.args)

    def test_success_consumes_display_even_if_draft_is_empty_again(self):
        d=self.show()
        self.assertEqual(self.accept(d),d.preview.text)
        with self.assertRaises(Refused):
            self.accept(d)
        self.assertEqual(self.ctx.session.history,())

    def test_replaced_display_cannot_accept_identical_digest(self):
        first=self.show()
        second=self.show()
        self.assertEqual(first.preview.confirmation_digest,second.preview.confirmation_digest)
        for stale in (first,Display(second.preview)):
            with self.assertRaises(Refused):
                self.accept(stale)
        self.assertEqual(self.accept(second),second.preview.text)

    def test_invalidation_and_failed_replacement_remove_old_display(self):
        d=self.show()
        self.control.invalidate()
        with self.assertRaises(Refused):
            self.accept(d)
        d=self.show()
        with self.assertRaises(Refused):
            self.control.show(replace(self.ctx,draft="new"),*self.args)
        with self.assertRaises(Refused):
            self.accept(d)

    def test_failed_current_confirmation_consumes_display(self):
        d=self.show()
        with self.assertRaises(Refused):
            self.accept(d,confirmation="0"*64)
        with self.assertRaises(Refused):
            self.accept(d)
        d=self.show()
        with self.assertRaises(Refused):
            self.accept(d,context=replace(self.ctx,generation="changed"))
        with self.assertRaises(Refused):
            self.accept(d)

    def test_revocation_and_epoch_cancel_are_terminal(self):
        d=self.show()
        self.jobs[0].cancel()
        with self.assertRaises(Refused):
            self.accept(d)
        with self.assertRaises(Refused):
            self.accept(d)
        with self.assertRaises(Refused):
            self.show()

    def test_foreign_thread_cannot_consume_or_invalidate_display(self):
        d=self.show()
        failures=[]
        def worker():
            for action in (self.control.invalidate,lambda:self.accept(d)):
                try:
                    action()
                except Refused as exc:
                    failures.append(str(exc))
        thread=Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(failures,["draft_display_thread"]*2)
        self.assertEqual(self.accept(d),d.preview.text)
