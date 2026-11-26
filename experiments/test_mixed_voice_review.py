# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from transcription_result import TranscriptFixture,Transcript
from mixed_voice_review import split_pcm,MixedVoiceReview
from test_speech_result import wav

class MixedVoiceTests(unittest.TestCase):
    def setUp(self):
        self.data=wav(b"\x01\x00"*4000+b"\x02\x00"*4000+b"\x03\x00"*4000)
        self.cuts=(0,4000,8000,12000)
        self.pieces=split_pcm(self.data,self.cuts)
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.rules=tuple(profiles.Rule(x,(),"allow") for x in ("host_shell","filesystem_read","filesystem_write"))
        self.g=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())

    def make(self,*,gateway=None,languages=("tr","en","de"),model="a"*64):
        g=self.g if gateway is None else gateway
        out=[]
        for i,(piece,lang,text) in enumerate(zip(self.pieces,languages,("Merhaba","Hello","Guten Tag"))):
            call=CapabilityCall("whisper_cpp","fixture","transcribe_wav",
                    (("artifact","segment-"+str(i)),("digest",piece.digest),("model_digest",model),
                     ("language",lang),("output","text-"+str(i))))
            req=call.request(g.context)
            job=TranscriptFixture(g,g.context,call,g.fixture_grant("stt-"+str(i),req,60))
            job.complete(Transcript(req.target,piece.digest,model,lang,"text-"+str(i),text))
            out.append(job)
        return tuple(out)

    def test_exact_full_pcm_coverage(self):
        self.assertEqual(b"".join(s.data[44:] for s in self.pieces),self.data[44:])
        self.assertEqual(len({s.digest for s in self.pieces}),3)
        for bad in ((1,4000,12000),(0,4000,8000),(0,4000,4000,12000),
                    (0,100,12000),[0,4000,12000],(False,4000,12000)):
            with self.assertRaises(Refused):
                split_pcm(self.data,bad)

    def test_mixed_preview_preserves_exact_sequence_and_boundaries(self):
        jobs=self.make()
        preview=MixedVoiceReview(self.data,self.cuts,jobs,"a"*64).preview()
        self.assertEqual(preview.text,"Merhaba\nHello\nGuten Tag")
        self.assertEqual(preview.audio_digest,sha256(self.data).hexdigest())
        self.assertEqual(preview.text_digest,sha256(preview.text.encode()).hexdigest())
        self.assertEqual([(s.start_frame,s.end_frame,s.language) for s in preview.segments],
                         [(0,4000,"tr"),(4000,8000,"en"),(8000,12000,"de")])

    def test_swapped_duplicate_and_wrong_source_denied(self):
        jobs=self.make()
        for bad in ((jobs[1],jobs[0],jobs[2]),(jobs[0],jobs[0],jobs[2])):
            with self.assertRaises(Refused):
                MixedVoiceReview(self.data,self.cuts,bad,"a"*64)
        changed=wav(b"\x04\x00"*4000+self.data[44+8000:])
        with self.assertRaises(Refused):
            MixedVoiceReview(changed,self.cuts,jobs,"a"*64)

    def test_wrong_model_and_cross_gateway_denied(self):
        jobs=self.make()
        with self.assertRaises(Refused):
            MixedVoiceReview(self.data,self.cuts,jobs,"b"*64)
        other=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())
        others=self.make(gateway=other)
        with self.assertRaises(Refused):
            MixedVoiceReview(self.data,self.cuts,(jobs[0],others[1],jobs[2]),"a"*64)

    def test_revoked_segment_invalidates_fresh_review(self):
        jobs=self.make()
        review=MixedVoiceReview(self.data,self.cuts,jobs,"a"*64)
        jobs[1].cancel()
        with self.assertRaises(Refused):
            review.preview()

    def test_monolingual_not_mislabeled_mixed(self):
        jobs=self.make(languages=("tr","tr","tr"))
        with self.assertRaises(Refused):
            MixedVoiceReview(self.data,self.cuts,jobs,"a"*64)


    def test_pause_proposal_preserves_all_pcm_frames(self):
        from mixed_voice_review import silence_boundaries
        voiced=b"\x00\x04"*24000
        quiet=b"\0\0"*12800
        data=wav(voiced+quiet+voiced+quiet+voiced)
        cuts=silence_boundaries(data)
        self.assertEqual(cuts,(0,30400,67200,97600))
        self.assertEqual(b"".join(p.data[44:] for p in split_pcm(data,cuts)),data[44:])

    def test_pause_proposal_holds_without_sufficient_interior_pause(self):
        from mixed_voice_review import silence_boundaries
        voiced=b"\x00\x04"*24000
        for body in (voiced*2,voiced+b"\0\0"*8000+voiced,b"\0\0"*12800+voiced+b"\0\0"*12800):
            with self.assertRaises(Refused):
                silence_boundaries(wav(body))

    def test_pause_proposal_holds_short_or_excessive_segments(self):
        from mixed_voice_review import silence_boundaries
        voiced=b"\x00\x04"*4000
        quiet=b"\0\0"*12800
        for body in (voiced+quiet+voiced, (b"\x00\x04"*16000+quiet)*9+b"\x00\x04"*16000):
            with self.assertRaises(Refused):
                silence_boundaries(wav(body))

    def test_pause_proposal_holds_noise_only_segment(self):
        from mixed_voice_review import silence_boundaries
        voiced=b"\x00\x04"*24000
        quiet=b"\x01\x00"*12800
        data=wav(voiced+quiet+voiced)
        self.assertEqual(silence_boundaries(data),(0,30400,60800))
