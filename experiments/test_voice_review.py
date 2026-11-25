import unittest
from io import BytesIO
import wave
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Candidate
from voice_gate import Config,VoiceGate
from test_voice_gate import LOUD,QUIET
from voice_review import target,review,wav_bytes

class VoiceReviewTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),profiles.Rule("local_audio_device",(),"allow"),),clock=profiles.PolicyClock())
        cfg=Config("fixture",600,3,2,12)
        v=VoiceGate(self.g,self.ctx,cfg)
        v.arm(self.g.fixture_grant("capture",Request(self.ctx,"capture",cfg.target(),"local","private"),60))
        for f in (LOUD,LOUD,LOUD,QUIET,QUIET):
            v.feed(f)
        self.pcm=v.take()
        self.c=Candidate("whisper-fixture","stt","local","whisper",True,True,4,80)
        self.grant=self.g.fixture_grant("stt",Request(self.ctx,"engine",target(self.c,"voice",self.pcm),"local"),60)
    def test_review_only_wav_and_exact_text(self):
        with wave.open(BytesIO(wav_bytes(self.pcm)),"rb") as w:
            self.assertEqual((w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes()),(1,2,16000,960))
            self.assertEqual(w.readframes(960),self.pcm)
        d=review(self.g,self.ctx,self.c,"voice",self.pcm,self.grant,(("text","Merhaba"),("completed",None)))
        self.assertEqual((d.pcm_digest,d.text),(sha256(self.pcm).hexdigest(),"Merhaba"))
        with self.assertRaises(Refused):
            review(self.g,self.ctx,self.c,"voice",self.pcm,self.grant,(("text","again"),("completed",None)))
    def test_changed_pcm_or_revoked_grant_rejected(self):
        events=(("text","mavi"),("completed",None))
        with self.assertRaises(Refused):
            review(self.g,self.ctx,self.c,"voice",QUIET*3,self.grant,events)
        self.g.revoke("stt")
        with self.assertRaises(Refused):
            review(self.g,self.ctx,self.c,"voice",self.pcm,self.grant,events)
