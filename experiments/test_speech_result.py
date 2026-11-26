# SPDX-License-Identifier: AGPL-3.0-only
import io
import wave
import unittest
from hashlib import sha256
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from speech_result import SpeechFixture,SpeechResult,pcm_info

def wav(samples=b"\x01\x00"*4000):
    f=io.BytesIO()
    with wave.open(f,"wb") as out:
        out.setparams((1,2,16000,0,"NONE","not compressed"))
        out.writeframes(samples)
    return f.getvalue()

class SpeechResultTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.rules=tuple(profiles.Rule(a,(),"allow") for a in ("host_shell","filesystem_read","filesystem_write"))
        self.g=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())
        self.call=CapabilityCall("espeak_ng","1.51","text_to_wav",
                    (("artifact","text"),("digest","a"*64),("language","tr"),("output","audio")))
        self.req=self.call.request(self.ctx)
        self.grant=self.g.fixture_grant("voice",self.req,60)
        self.job=SpeechFixture(self.g,self.ctx,self.call,self.grant)
        self.value=SpeechResult(self.req.target,"a"*64,"tr","audio",wav())
    def test_exact_pcm_receipt(self):
        self.job.checkpoint()
        self.job.complete(self.value)
        self.assertEqual(self.job.result(),(self.req.target,"a"*64,"tr","audio",
                                            sha256(self.value.data).hexdigest(),16000,4000))
        with self.assertRaises(Refused):
            self.job.complete(self.value)
    def test_invalid_riff_and_silent_pcm(self):
        data=wav()
        for bad in (data+b"x",data[:43],data[:12]+b"JUNK"+data[16:],wav(b"\0"*8000)):
            with self.assertRaises(Refused):
                pcm_info(bad)
    def test_wrong_language_source_and_output_denied(self):
        for changes in ({"language":"de"},{"source_digest":"b"*64},{"output":"other"},{"target":"b"*64}):
            self.setUp()
            with self.assertRaises(Refused):
                self.job.complete(replace(self.value,**changes))
            with self.assertRaises(Refused):
                self.job.result()
    def test_revoked_completed_output_denied(self):
        self.job.complete(self.value)
        self.g.revoke("voice")
        with self.assertRaises(Refused):
            self.job.result()
    def test_missing_write_and_cloud_actor_denied(self):
        other=FixtureGateway(self.ctx,self.rules[:2],clock=profiles.PolicyClock())
        with self.assertRaises(Refused):
            other.fixture_grant("no",self.req,60)
        with self.assertRaises(Refused):
            self.g.fixture_grant("cloud",self.call.request(self.ctx,actor="cloud"),60)
    def test_mixed_language_not_supported_by_single_voice(self):
        with self.assertRaises(Refused):
            replace(self.call,args=self.call.args[:2]+(("language","tr-en-de"),)+self.call.args[3:]).checked()

    def test_audio_conversion_binds_completed_speech_and_separate_grant(self):
        from conversion_result import ConversionResult
        self.job.complete(self.value)
        call=self.job.prepare_conversion("4.4","flac")
        args=dict(call.args)
        self.assertEqual(args,{"artifact":"audio","digest":sha256(self.value.data).hexdigest(),"output":"flac"})
        req=call.request(self.ctx)
        grant=self.g.fixture_grant("conversion",req,60)
        child=self.job.start_conversion("4.4","flac",grant)
        data=b"fLaCsynthetic"
        child.complete(ConversionResult(req.target,args["digest"],"flac","flac",data))
        self.assertEqual(child.result()[4],sha256(data).hexdigest())
        self.job.cancel()
        with self.assertRaises(Refused):
            child.result()
    def test_audio_conversion_no_alias_or_incomplete_speech(self):
        with self.assertRaises(Refused):
            self.job.prepare_conversion("4.4","flac")
        self.job.complete(self.value)
        for output in ("audio","text"):
            with self.assertRaises(Refused):
                self.job.prepare_conversion("4.4",output)
    def test_audio_conversion_wrong_digest_or_revoked_parent_denied(self):
        self.job.complete(self.value)
        call=self.job.prepare_conversion("4.4","flac")
        wrong=replace(call,args=(call.args[0],("digest","c"*64),call.args[2]))
        grant=self.g.fixture_grant("wrong",wrong.request(self.ctx),60)
        with self.assertRaises(Refused):
            self.job.start_conversion("4.4","flac",grant)
        exact=self.g.fixture_grant("exact",call.request(self.ctx),60)
        self.g.revoke("voice")
        with self.assertRaises(Refused):
            self.job.start_conversion("4.4","flac",exact)
