# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import replace
import unittest
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from capabilities import CapabilityCall
from language_detection import Detection,DetectionFixture,parse_detection
from transcription_result import Transcript

class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,tuple(profiles.Rule(x,(),"allow") for x in
                   ("host_shell","filesystem_read","filesystem_write")),clock=profiles.PolicyClock())
        self.call=CapabilityCall("whisper_cpp","fixture","detect_language",
                (("artifact","audio"),("digest","a"*64),("model_digest","b"*64),("output","language")))
        self.req=self.call.request(self.ctx)
        self.grant=self.g.fixture_grant("detect",self.req,60)
        self.job=DetectionFixture(self.g,self.ctx,self.call,self.grant)
        self.value=Detection(self.req.target,"a"*64,"b"*64,"language","tr",900000)

    def test_strict_detection_log(self):
        line=b"whisper_full_with_state: auto-detected language: tr (p = 0.900000)"
        self.assertEqual(parse_detection(b"prefix\n"+line+b"\nsuffix"),("tr",900000))
        for bad in (line+ b"\n"+line,line.replace(b"tr ",b"fr "),line.replace(b"0.900000",b"0.499999"),
                    line.replace(b"0.900000",b"1.100000"),line+b" extra",b"x"*65537):
            with self.assertRaises(Refused):
                parse_detection(bad)
    def test_detection_binds_source_model_language_and_child(self):
        self.job.complete(self.value)
        call=self.job.prepare_transcription("text")
        args=dict(call.args)
        self.assertEqual(args,{"artifact":"audio","digest":"a"*64,"model_digest":"b"*64,"language":"tr","output":"text"})
        req=call.request(self.ctx)
        child=self.job.start_transcription("text",self.g.fixture_grant("child",req,60))
        child.complete(Transcript(req.target,"a"*64,"b"*64,"tr","text","Merhaba"))
        self.job.cancel()
        with self.assertRaises(Refused):
            child.result()
    def test_unfinished_and_aliased_output_denied(self):
        with self.assertRaises(Refused):
            self.job.prepare_transcription("text")
        self.job.complete(self.value)
        for output in ("audio","language"):
            with self.assertRaises(Refused):
                self.job.prepare_transcription(output)
    def test_low_confidence_wrong_model_unsupported_language_held(self):
        for changes in ({"confidence_ppm":499999},{"model_digest":"c"*64},{"language":"fr"},{"confidence_ppm":True}):
            self.setUp()
            with self.assertRaises(Refused):
                self.job.complete(replace(self.value,**changes))
            with self.assertRaises(Refused):
                self.job.result()
    def test_wrong_transcription_grant_denied(self):
        self.job.complete(self.value)
        exact=self.job.prepare_transcription("text")
        bad=replace(exact,args=tuple((k,"de" if k=="language" else v) for k,v in exact.args))
        grant=self.g.fixture_grant("bad",bad.request(self.ctx),60)
        with self.assertRaises(Refused):
            self.job.start_transcription("text",grant)


    def test_observation_does_not_authorize_low_or_unsupported_language(self):
        from language_detection import detection_observation
        for lang,score in (("tr",499999),("fr",990000)):
            raw=("whisper_full_with_state: auto-detected language: "+lang+" (p = "+format(score/1000000,".6f")+")").encode()
            self.assertEqual(detection_observation(raw),(lang,score))
            with self.assertRaisesRegex(Refused,"language_detection_hold"):
                parse_detection(raw)


    def test_owner_override_is_distinct_from_auto_detection(self):
        raw=b"whisper_full_with_state: auto-detected language: ko (p = 0.107022)"
        self.assertEqual(self.job.hold_observation(raw)[:2],("ko",107022))
        with self.assertRaises(Refused):
            self.job.result()
        with self.assertRaises(Refused):
            self.job.prepare_transcription("text")
        call,review=self.job.prepare_override("tr","text")
        req=call.request(self.ctx)
        grant=self.g.fixture_grant("corrected",req,60)
        with self.assertRaises(Refused):
            self.job.start_override("tr","text",grant,None)
        approved=self.g.fixture_grant("owner-choice",review,60)
        trans=self.job.start_override("tr","text",grant,approved)
        trans.complete(Transcript(req.target,"a"*64,"b"*64,"tr","text","Merhaba"))
        self.assertEqual(trans.result()[0].language,"tr")
        self.g.revoke("owner-choice")
        with self.assertRaises(Refused):
            trans.result()

    def test_override_binds_exact_language_output_and_single_use(self):
        self.job.hold_observation(b"whisper_full_with_state: auto-detected language: ko (p = 0.107022)")
        call,review=self.job.prepare_override("tr","text")
        approved=self.g.fixture_grant("owner-choice",review,60)
        for lang,out in (("en","text"),("tr","other")):
            changed,_=self.job.prepare_override(lang,out)
            grant=self.g.fixture_grant("changed-"+lang+"-"+out,changed.request(self.ctx),60)
            with self.assertRaises(Refused):
                self.job.start_override(lang,out,grant,approved)
        grant=self.g.fixture_grant("exact",call.request(self.ctx),60)
        self.job.start_override("tr","text",grant,approved)
        with self.assertRaises(Refused):
            self.job.start_override("tr","text",self.g.fixture_grant("again",call.request(self.ctx),60),approved)

    def test_override_requires_real_hold_and_live_parent(self):
        with self.assertRaises(Refused):
            self.job.prepare_override("tr","text")
        with self.assertRaises(Refused):
            self.job.hold_observation(b"whisper_full_with_state: auto-detected language: tr (p = 0.990000)")
        self.job.hold_observation(b"whisper_full_with_state: auto-detected language: ko (p = 0.107022)")
        call,review=self.job.prepare_override("tr","text")
        grant=self.g.fixture_grant("exact",call.request(self.ctx),60)
        approved=self.g.fixture_grant("owner-choice",review,60)
        self.job.cancel()
        with self.assertRaises(Refused):
            self.job.start_override("tr","text",grant,approved)
