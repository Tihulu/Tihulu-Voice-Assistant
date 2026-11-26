# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from test_speech_result import wav
from mixed_voice_review import silence_boundaries
from manual_mixed_boundary import boundary_preview,verify_boundary_preview

class ManualMixedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.data=wav(b"\x00\x04"*24000+b"\x00\x05"*24000+b"\x00\x06"*24000)
        self.cuts=(0,24000,48000,72000)

    def test_no_pause_requires_explicit_boundaries_and_preserves_every_frame(self):
        with self.assertRaises(Refused):
            silence_boundaries(self.data)
        shown=boundary_preview(self.data,self.cuts)
        pieces=verify_boundary_preview(self.data,self.cuts,shown)
        self.assertEqual(b"".join(p.data[44:] for p in pieces),self.data[44:])
        self.assertEqual(tuple(p.digest for p in pieces),tuple(r.audio_digest for r in shown.rows))
        self.assertEqual(shown.cuts,self.cuts)

    def test_recut_or_changed_source_invalidates_preview(self):
        shown=boundary_preview(self.data,self.cuts)
        for data,cuts in ((self.data,(0,20000,48000,72000)),
                          (wav(b"\x00\x07"*72000),self.cuts)):
            with self.assertRaises(Refused):
                verify_boundary_preview(data,cuts,shown)

    def test_tampered_or_missing_snapshot_rejected(self):
        shown=boundary_preview(self.data,self.cuts)
        for bad in (None,shown.preview_digest,replace(shown,preview_digest="0"*64),
                    replace(shown,rows=shown.rows[::-1])):
            with self.assertRaises(Refused):
                verify_boundary_preview(self.data,self.cuts,bad)

    def test_malformed_or_too_short_boundaries_rejected(self):
        for bad in ((0,10000,72000),(0,24000,72001),(0,24000,24000,72000),
                    (False,24000,72000),[0,24000,72000]):
            with self.assertRaises(Refused):
                boundary_preview(self.data,bad)


    def _live_transcripts(self,gateway=None):
        from tihulu_jarvis import profiles
        from gateway import Context,FixtureGateway
        from capabilities import CapabilityCall
        from transcription_result import TranscriptFixture,Transcript
        ctx=Context("manual-task","session","profile",1,"local","LOCAL_ONLY")
        rules=tuple(profiles.Rule(x,(),"allow") for x in ("host_shell","filesystem_read","filesystem_write"))
        g=gateway if gateway is not None else FixtureGateway(ctx,rules,clock=profiles.PolicyClock())
        pieces=verify_boundary_preview(self.data,self.cuts,boundary_preview(self.data,self.cuts))
        jobs=[]
        for i,(piece,lang,text) in enumerate(zip(pieces,("tr","en","de"),("Merhaba","Hello","Guten Tag"))):
            call=CapabilityCall("whisper_cpp","fixture","transcribe_wav",
                    (("artifact","segment-"+str(i)),("digest",piece.digest),("model_digest","a"*64),
                     ("language",lang),("output","text-"+str(i))))
            req=call.request(g.context)
            job=TranscriptFixture(g,g.context,call,g.fixture_grant("stt-"+str(i),req,60))
            job.complete(Transcript(req.target,piece.digest,"a"*64,lang,"text-"+str(i),text))
            jobs.append(job)
        return tuple(jobs)

    def test_manual_review_ready_preserves_original_and_edits(self):
        from manual_mixed_boundary import inspect_manual_review
        shown=boundary_preview(self.data,self.cuts)
        jobs=self._live_transcripts()
        first=inspect_manual_review(self.data,self.cuts,shown,jobs,"a"*64)
        self.assertEqual(first.status,"READY")
        row=first.preview.segments[0]
        edited=inspect_manual_review(self.data,self.cuts,shown,jobs,"a"*64,
                                      ((0,row.original.transcript_digest,"Merhaba dünya"),))
        self.assertEqual(edited.status,"READY")
        self.assertEqual(edited.preview.segments[0].original,row.original)
        self.assertEqual(edited.preview.text,"Merhaba dünya\nHello\nGuten Tag")
        self.assertEqual(inspect_manual_review(self.data,self.cuts,shown,jobs,"a"*64),first)

    def test_manual_review_stale_boundary_never_exposes_text(self):
        from manual_mixed_boundary import inspect_manual_review
        shown=boundary_preview(self.data,self.cuts)
        jobs=self._live_transcripts()
        for cuts,snapshot in (((0,24001,48000,72000),shown),(self.cuts,None)):
            result=inspect_manual_review(self.data,cuts,snapshot,jobs,"a"*64)
            self.assertEqual(result.status,"HOLD")
            self.assertIsNone(result.preview)

    def test_manual_review_rejects_wrong_model_receipt_order_and_edit(self):
        from manual_mixed_boundary import inspect_manual_review
        shown=boundary_preview(self.data,self.cuts)
        jobs=self._live_transcripts()
        for selected,model,edits in ((jobs,"b"*64,()),(jobs[::-1],"a"*64,()),
                                     (jobs[:2],"a"*64,()),(jobs,"a"*64,((0,"0"*64,"Edit"),))):
            result=inspect_manual_review(self.data,self.cuts,shown,selected,model,edits)
            self.assertEqual(result.status,"HOLD")
            self.assertIsNone(result.preview)

    def test_manual_review_revocation_and_exact_expiry_hide_text(self):
        from unittest.mock import patch
        from manual_mixed_boundary import inspect_manual_review
        shown=boundary_preview(self.data,self.cuts)
        jobs=self._live_transcripts()
        jobs[1].cancel()
        outcome=inspect_manual_review(self.data,self.cuts,shown,jobs,"a"*64)
        self.assertEqual((outcome.status,outcome.preview),("HOLD",None))
        with patch("tihulu_jarvis.profiles.time.time",return_value=1000), \
             patch("tihulu_jarvis.profiles.time.monotonic",return_value=200):
            jobs=self._live_transcripts()
            self.assertEqual(inspect_manual_review(self.data,self.cuts,shown,jobs,"a"*64).status,"READY")
        with patch("tihulu_jarvis.profiles.time.time",return_value=1060), \
             patch("tihulu_jarvis.profiles.time.monotonic",return_value=260):
            outcome=inspect_manual_review(self.data,self.cuts,shown,jobs,"a"*64)
            self.assertEqual((outcome.status,outcome.reason,outcome.preview),("HOLD","expired",None))
