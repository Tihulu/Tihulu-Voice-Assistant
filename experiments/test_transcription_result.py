# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from transcription_result import TranscriptFixture,Transcript

class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.rules=tuple(profiles.Rule(a,(),"allow") for a in ("host_shell","filesystem_read","filesystem_write"))
        self.g=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())
        self.call=CapabilityCall("whisper_cpp","fixture","transcribe_wav",
                   (("artifact","audio"),("digest","a"*64),("model_digest","b"*64),
                    ("language","en"),("output","transcript")))
        self.req=self.call.request(self.ctx)
        self.grant=self.g.fixture_grant("voice",self.req,60)
        self.job=TranscriptFixture(self.g,self.ctx,self.call,self.grant)
        self.value=Transcript(self.req.target,"a"*64,"b"*64,"en","transcript","Hello")
    def test_exact_transcript(self):
        self.job.complete(self.value)
        self.assertEqual(self.job.result(),(self.value,sha256(b"Hello").hexdigest()))
    def test_wrong_model_source_language_and_target_denied(self):
        for change in ({"model_digest":"c"*64},{"source_digest":"c"*64},{"language":"de"},
                       {"output":"other"},{"target":"c"*64}):
            self.setUp()
            with self.assertRaises(Refused):
                self.job.complete(replace(self.value,**change))
            with self.assertRaises(Refused):
                self.job.result()
    def test_empty_oversized_or_control_text_denied(self):
        for value in (" ","x"*16385,"secret\0"):
            self.setUp()
            with self.assertRaises(Refused):
                self.job.complete(replace(self.value,text=value))
    def test_revocation_and_cloud_denied(self):
        self.job.complete(self.value)
        self.g.revoke("voice")
        with self.assertRaises(Refused):
            self.job.result()
        with self.assertRaises(Refused):
            self.g.fixture_grant("cloud",self.call.request(self.ctx,actor="cloud"),60)
