# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Refused
from inference import Candidate
from adapter_protocol import LocalAdapter,prepare,local_request
from speech_result import SpeechResult
from test_speech_result import wav

class VoiceChainTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.rules=tuple(profiles.Rule(x,(),"allow") for x in ("host_shell","filesystem_read","filesystem_write"))
        self.g=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())
        self.c=Candidate("fixture-model","llm","local","jan",True,True,100,50)
        self.req=local_request(self.ctx,prepare(self.c,"turn","llm",b"fixture prompt"))
        self.grant=self.g.fixture_grant("turn",self.req,60)
        self.adapter=LocalAdapter(self.g,self.ctx,self.c,"turn","llm",b"fixture prompt",self.grant)

    def complete_turn(self):
        self.adapter.event("turn",1,"text","Merhaba")
        self.adapter.event("turn",2,"completed",None)

    def test_exact_completed_local_response_to_speech(self):
        self.complete_turn()
        call=self.adapter.prepare_speech("1.50","tr","turn-text","turn-audio")
        self.assertEqual(dict(call.args)["digest"],sha256("Merhaba".encode()).hexdigest())
        req=call.request(self.ctx)
        grant=self.g.fixture_grant("speech",req,60)
        job,payload=self.adapter.start_speech("1.50","tr","turn-text","turn-audio",grant)
        self.assertEqual(payload,"Merhaba".encode())
        job.complete(SpeechResult(req.target,sha256(payload).hexdigest(),"tr","turn-audio",wav()))
        self.assertEqual(job.result()[2],"tr")
        self.g.revoke("turn")
        with self.assertRaises(Refused):
            job.result()

    def test_incomplete_response_and_alias_denied(self):
        with self.assertRaises(Refused):
            self.adapter.prepare_speech("1.50","en","text","audio")
        self.complete_turn()
        with self.assertRaises(Refused):
            self.adapter.prepare_speech("1.50","en","text","text")

    def test_mismatched_voice_grant_and_revoked_parent_denied(self):
        self.complete_turn()
        exact=self.adapter.prepare_speech("1.50","tr","text","audio")
        wrong=replace(exact,args=exact.args[:2]+(("language","de"),)+exact.args[3:])
        grant=self.g.fixture_grant("wrong",wrong.request(self.ctx),60)
        with self.assertRaises(Refused):
            self.adapter.start_speech("1.50","tr","text","audio",grant)
        grant=self.g.fixture_grant("exact",exact.request(self.ctx),60)
        self.adapter.cancel()
        with self.assertRaises(Refused):
            self.adapter.start_speech("1.50","tr","text","audio",grant)

    def test_model_read_permission_does_not_grant_speech_write(self):
        self.complete_turn()
        call=self.adapter.prepare_speech("1.50","tr","text","audio")
        other=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        with self.assertRaises(Refused):
            other.fixture_grant("speech",call.request(self.ctx),60)

class TranscriptVoiceChainTests(unittest.TestCase):
    def setUp(self):
        from capabilities import CapabilityCall
        from transcription_result import TranscriptFixture,Transcript
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.rules=tuple(profiles.Rule(x,(),"allow") for x in ("host_shell","filesystem_read","filesystem_write"))
        self.g=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())
        self.c=Candidate("fixture-model","llm","local","jan",True,True,100,50)
        call=CapabilityCall("whisper_cpp","fixture","transcribe_wav",
                   (("artifact","audio"),("digest","a"*64),("model_digest","b"*64),
                    ("language","tr"),("output","transcript")))
        req=call.request(self.ctx)
        self.grant=self.g.fixture_grant("transcribe",req,60)
        self.stt=TranscriptFixture(self.g,self.ctx,call,self.grant)
        self.value=Transcript(req.target,"a"*64,"b"*64,"tr","transcript","Merhaba")

    def start_turn(self):
        self.stt.complete(self.value)
        req=self.stt.prepare_local_turn(self.c,"turn","de")
        grant=self.g.fixture_grant("turn",req,60)
        return self.stt.start_local_turn(self.c,"turn","de",grant)

    def test_exact_transcript_turn_payload(self):
        import json
        turn,payload=self.start_turn()
        obj=json.loads(payload)
        self.assertEqual(obj["transcript"],"Merhaba")
        self.assertEqual(obj["transcript_digest"],sha256(b"Merhaba").hexdigest())
        self.assertEqual(obj["source_digest"],"a"*64)
        self.assertEqual(obj["model_digest"],"b"*64)
        self.assertEqual(obj["input_language"],"tr")
        self.assertEqual(obj["response_language"],"de")
        self.assertEqual(turn.call.input_digest,sha256(payload).hexdigest())

    def test_incomplete_cloud_invalid_language_denied(self):
        with self.assertRaises(Refused):
            self.stt.prepare_local_turn(self.c,"turn","de")
        self.stt.complete(self.value)
        with self.assertRaises(Refused):
            self.stt.prepare_local_turn(replace(self.c,locality="cloud"),"turn","de")
        with self.assertRaises(Refused):
            self.stt.prepare_local_turn(self.c,"turn","auto")

    def test_changed_language_cannot_reuse_grant(self):
        self.stt.complete(self.value)
        req=self.stt.prepare_local_turn(self.c,"turn","de")
        grant=self.g.fixture_grant("turn",req,60)
        with self.assertRaises(Refused):
            self.stt.start_local_turn(self.c,"turn","en",grant)

    def test_three_stage_chain_revoked_transcript_invalidates_all(self):
        turn,_=self.start_turn()
        turn.event("turn",1,"text","Guten Tag")
        turn.event("turn",2,"completed",None)
        call=turn.prepare_speech("1.50","de","turn-text","turn-audio")
        req=call.request(self.ctx)
        grant=self.g.fixture_grant("speech",req,60)
        voice,payload=turn.start_speech("1.50","de","turn-text","turn-audio",grant)
        voice.complete(SpeechResult(req.target,sha256(payload).hexdigest(),"de","turn-audio",wav()))
        self.assertEqual(voice.result()[2],"de")
        self.stt.cancel()
        for result in (turn.result,voice.result):
            with self.assertRaises(Refused):
                result()

    def test_revoked_transcript_before_local_turn_denied(self):
        self.stt.complete(self.value)
        req=self.stt.prepare_local_turn(self.c,"turn","de")
        grant=self.g.fixture_grant("turn",req,60)
        self.g.revoke("transcribe")
        with self.assertRaises(Refused):
            self.stt.start_local_turn(self.c,"turn","de",grant)
