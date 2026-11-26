# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from manual_mixed_boundary import boundary_preview
import test_manual_mixed_boundary as manual_tests
from tihulu_jarvis.voice.session import Session
from voice_composer_handoff import ComposerContext,prepare_handoff,confirmed_draft

class VoiceComposerHandoffTests(unittest.TestCase):
    def setUp(self):
        fixture=manual_tests.ManualMixedBoundaryTests()
        fixture.setUp()
        self.data,self.cuts=fixture.data,fixture.cuts
        self.shown=boundary_preview(self.data,self.cuts)
        self.jobs=fixture._live_transcripts()
        self.session=Session()
        self.ctx=ComposerContext(self.session,0,"fixture-profile","generation-1",
                                 "local-route","fixture-model","",False,False,False)
        self.args=(self.data,self.cuts,self.shown,self.jobs,"a"*64)

    def test_explicit_handoff_returns_only_text_and_no_session_effect(self):
        p=prepare_handoff(self.ctx,*self.args)
        text=confirmed_draft(self.ctx,p,p.confirmation_digest,*self.args)
        self.assertEqual(text,"Merhaba\nHello\nGuten Tag")
        self.assertEqual(self.session.history,())
        self.assertEqual(self.session.epoch,0)
        self.assertIsNone(self.session.active)
        self.assertEqual(self.ctx.draft,"")

    def test_draft_attachment_busy_close_or_route_change_denied(self):
        p=prepare_handoff(self.ctx,*self.args)
        for ctx in (replace(self.ctx,draft=" "),replace(self.ctx,draft="Existing"),
                    replace(self.ctx,has_attachment=True),replace(self.ctx,busy=True),
                    replace(self.ctx,closing=True),replace(self.ctx,generation="generation-2"),
                    replace(self.ctx,profile="other"),replace(self.ctx,route="other"),
                    replace(self.ctx,model="other"),replace(self.ctx,session=Session())):
            with self.assertRaises(Refused):
                confirmed_draft(ctx,p,p.confirmation_digest,*self.args)
        for ctx in (replace(self.ctx,draft=" "),replace(self.ctx,has_attachment=True),
                    replace(self.ctx,busy=True),replace(self.ctx,closing=True)):
            with self.assertRaises(Refused):
                prepare_handoff(ctx,*self.args)

    def test_cancelled_epoch_denied_even_if_context_is_replayed(self):
        p=prepare_handoff(self.ctx,*self.args)
        self.session.invalidate()
        with self.assertRaises(Refused):
            confirmed_draft(self.ctx,p,p.confirmation_digest,*self.args)

    def test_missing_confirmation_and_changed_edits_denied(self):
        p=prepare_handoff(self.ctx,*self.args)
        for bad in (None,"0"*64,True):
            with self.assertRaises(Refused):
                confirmed_draft(self.ctx,p,bad,*self.args)
        from manual_mixed_boundary import inspect_manual_review
        view=inspect_manual_review(*self.args).preview
        edit=((0,view.segments[0].original.transcript_digest,"Merhaba dünya"),)
        with self.assertRaises(Refused):
            confirmed_draft(self.ctx,p,p.confirmation_digest,*self.args,edit)
        edited=prepare_handoff(self.ctx,*self.args,edit)
        self.assertEqual(confirmed_draft(self.ctx,edited,edited.confirmation_digest,*self.args,edit),
                         "Merhaba dünya\nHello\nGuten Tag")

    def test_revocation_refuses_fresh_handoff(self):
        p=prepare_handoff(self.ctx,*self.args)
        self.jobs[0].cancel()
        with self.assertRaises(Refused):
            confirmed_draft(self.ctx,p,p.confirmation_digest,*self.args)

    def test_wrong_model_or_forged_preview_denied(self):
        p=prepare_handoff(self.ctx,*self.args)
        with self.assertRaises(Refused):
            confirmed_draft(self.ctx,p,p.confirmation_digest,*self.args[:-1],"b"*64)
        with self.assertRaises(Refused):
            confirmed_draft(self.ctx,replace(p,text="different"),p.confirmation_digest,*self.args)
