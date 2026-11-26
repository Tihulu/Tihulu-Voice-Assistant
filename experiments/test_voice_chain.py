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
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","de"),60)
        return self.stt.start_local_turn(self.c,"turn","de",grant,review_grant=review)

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

    def test_bound_response_language_cannot_be_replaced_by_voice(self):
        turn,_=self.start_turn()
        turn.event("turn",1,"text","Guten Tag")
        turn.event("turn",2,"completed",None)
        for lang in ("tr","en"):
            with self.assertRaisesRegex(Refused,"voice_language_mismatch"):
                turn.prepare_speech("1.50",lang,"turn-text","turn-audio")
        self.assertEqual(dict(turn.prepare_speech("1.50","de","turn-text","turn-audio").args)["language"],"de")
    def test_bound_language_not_valid_for_unrelated_payload(self):
        req=local_request(self.ctx,prepare(self.c,"other","llm",b"plain"))
        grant=self.g.fixture_grant("other",req,60)
        with self.assertRaises(Refused):
            LocalAdapter(self.g,self.ctx,self.c,"other","llm",b"plain",grant,response_language="de")

class NativeVoiceTransportTests(unittest.TestCase):
    def setUp(self):
        from capabilities import CapabilityCall
        from transcription_result import TranscriptFixture,Transcript
        from adapter_protocol import VOICE_NATIVE_MODEL,VOICE_NATIVE_PROVIDER
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        rules=tuple(profiles.Rule(x,(),"allow") for x in ("host_shell","filesystem_read","filesystem_write"))
        self.g=FixtureGateway(self.ctx,rules,clock=profiles.PolicyClock())
        self.c=Candidate(VOICE_NATIVE_MODEL,"llm","local",VOICE_NATIVE_PROVIDER,True,True,16,50)
        call=CapabilityCall("whisper_cpp","fixture","transcribe_wav",
                    (("artifact","audio"),("digest","a"*64),("model_digest","b"*64),
                     ("language","en"),("output","transcript")))
        req=call.request(self.ctx)
        grant=self.g.fixture_grant("stt",req,60)
        self.stt=TranscriptFixture(self.g,self.ctx,call,grant)
        self.stt.complete(Transcript(req.target,"a"*64,"b"*64,"en","transcript","Hello world"))

    def start(self):
        req=self.stt.prepare_local_turn(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","en",native_completion=True),60)
        return self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=review)
    def test_exact_native_bytes_not_envelope(self):
        import json
        turn,wire=self.start()
        self.assertEqual(json.loads(wire)["prompt"],"Hello world")
        self.assertNotEqual(turn.call.input_digest,sha256(wire).hexdigest())
        with self.assertRaises(Refused):
            turn.begin_fixture_transport(wire+b" ")
        turn.begin_fixture_transport(wire)
        turn.checkpoint_fixture_transport()
        with self.assertRaises(Refused):
            turn.begin_fixture_transport(wire)
    def test_native_grant_not_reusable_for_normalized_turn(self):
        req=self.stt.prepare_local_turn(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        with self.assertRaises(Refused):
            self.stt.start_local_turn(self.c,"turn","en",grant)
    def test_native_profile_and_language_restricted(self):
        for c,lang in ((replace(self.c,identity="other"),"en"),(self.c,"de")):
            with self.assertRaises(Refused):
                self.stt.prepare_local_turn(c,"turn",lang,native_completion=True)
    def test_native_transport_revoked_parent(self):
        turn,wire=self.start()
        self.stt.cancel()
        with self.assertRaises(Refused):
            turn.begin_fixture_transport(wire)

    def test_native_event_requires_started_transport(self):
        turn,_=self.start()
        with self.assertRaisesRegex(Refused,"native_voice_transport_required"):
            turn.event("turn",1,"text","unverified")
        with self.assertRaises(Refused):
            turn.result()
    def test_reserved_envelope_cannot_omit_language(self):
        req,envelope,_=self.stt._turn_input(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        with self.assertRaises(Refused):
            LocalAdapter(self.g,self.ctx,self.c,"turn","llm",envelope,grant,
                         dependencies=((self.stt._grant,self.stt._req),))
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","en",native_completion=True),60)
        turn,_=self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=review)
        self.assertEqual(turn.state,"ready")
    def test_exact_parent_grant_identity_not_substitutable(self):
        req,envelope,_=self.stt._turn_input(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        other=self.g.fixture_grant("other-stt",self.stt._req,60)
        self.g.claim(other,self.stt._req)
        with self.assertRaisesRegex(Refused,"invalid_voice_parent"):
            LocalAdapter(self.g,self.ctx,self.c,"turn","llm",envelope,grant,
                         dependencies=((other,self.stt._req),),response_language="en")
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","en",native_completion=True),60)
        turn,_=self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=review)
        self.assertEqual(turn.state,"ready")
    def test_forged_transcript_digest_denied_even_with_new_grant(self):
        import json
        _,envelope,_=self.stt._turn_input(self.c,"turn","en",native_completion=True)
        obj=json.loads(envelope)
        obj["transcript_digest"]="c"*64
        bad=json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
        req=local_request(self.ctx,prepare(self.c,"turn","llm",bad),dependent=True)
        grant=self.g.fixture_grant("bad",req,60)
        with self.assertRaises(Refused):
            LocalAdapter(self.g,self.ctx,self.c,"turn","llm",bad,grant,
                         dependencies=((self.stt._grant,self.stt._req),),response_language="en")

    def test_forged_self_consistent_transcript_denied(self):
        import json
        from adapter_protocol import native_voice_payload
        _,envelope,_=self.stt._turn_input(self.c,"turn","en",native_completion=True)
        obj=json.loads(envelope)
        obj["transcript"]="Changed text"
        obj["transcript_digest"]=sha256(b"Changed text").hexdigest()
        obj["native_wire_digest"]=sha256(native_voice_payload("Changed text")).hexdigest()
        bad=json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
        req=local_request(self.ctx,prepare(self.c,"turn","llm",bad),dependent=True)
        grant=self.g.fixture_grant("bad-text",req,60)
        with self.assertRaisesRegex(Refused,"voice_transcript_receipt_mismatch"):
            LocalAdapter(self.g,self.ctx,self.c,"turn","llm",bad,grant,
                         dependencies=((self.stt._grant,self.stt._req),),response_language="en")

    def test_forged_provenance_even_with_exact_parent_denied(self):
        import json
        _,envelope,_=self.stt._turn_input(self.c,"turn","en",native_completion=True)
        for i,key in enumerate(("artifact","source_digest","model_digest")):
            obj=json.loads(envelope)
            obj[key]="other" if key=="artifact" else "c"*64
            bad=json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
            req=local_request(self.ctx,prepare(self.c,"turn","llm",bad),dependent=True)
            grant=self.g.fixture_grant("bad-provenance"+str(i),req,60)
            with self.assertRaisesRegex(Refused,"voice_transcript_receipt_mismatch"):
                LocalAdapter(self.g,self.ctx,self.c,"turn","llm",bad,grant,
                             dependencies=((self.stt._grant,self.stt._req),),response_language="en")

    def test_uncompleted_transcript_grant_cannot_act_as_receipt(self):
        import json
        _,envelope,_=self.stt._turn_input(self.c,"turn","en",native_completion=True)
        obj=json.loads(envelope)
        other=self.g.fixture_grant("uncompleted",self.stt._req,60)
        self.g.claim(other,self.stt._req)
        obj["transcript_grant"]=other.identity
        bad=json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
        req=local_request(self.ctx,prepare(self.c,"turn","llm",bad),dependent=True)
        grant=self.g.fixture_grant("bad-parent",req,60)
        with self.assertRaisesRegex(Refused,"voice_transcript_receipt_mismatch"):
            LocalAdapter(self.g,self.ctx,self.c,"turn","llm",bad,grant,
                         dependencies=((other,self.stt._req),),response_language="en")

    def test_no_automatic_submit_and_review_single_use(self):
        req=self.stt.prepare_local_turn(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        with self.assertRaisesRegex(Refused,"voice_review_required"):
            self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True)
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","en",native_completion=True),60)
        turn,wire=self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=review)
        turn.begin_fixture_transport(wire)
        second=self.g.fixture_grant("second",req,60)
        with self.assertRaisesRegex(Refused,"voice_review_already_used"):
            self.stt.start_local_turn(self.c,"turn","en",second,native_completion=True,review_grant=review)
    def test_review_exact_model_identity_language_and_transport(self):
        req=self.stt.prepare_local_turn(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        wrong=self.g.fixture_grant("wrong-review",self.stt.prepare_review(self.c,"other","en",native_completion=True),60)
        with self.assertRaises(Refused):
            self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=wrong)
        wrong_mode=self.g.fixture_grant("wrong-mode",self.stt.prepare_review(self.c,"turn","en"),60)
        with self.assertRaises(Refused):
            self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=wrong_mode)
        review_req=self.stt.prepare_review(self.c,"turn","en",native_completion=True)
        for actor in ("local","cloud"):
            with self.assertRaises(Refused):
                self.g.fixture_grant("actor-"+actor,replace(review_req,actor=actor),60)
    def test_revoke_review_invalidates_completed_turn_and_speech(self):
        req=self.stt.prepare_local_turn(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","en",native_completion=True),60)
        turn,wire=self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=review)
        turn.begin_fixture_transport(wire)
        turn.event("turn",1,"text","Hello")
        turn.event("turn",2,"completed",None)
        call=turn.prepare_speech("1.50","en","text","audio")
        speech_req=call.request(self.ctx)
        speech_grant=self.g.fixture_grant("speech",speech_req,60)
        voice,payload=turn.start_speech("1.50","en","text","audio",speech_grant)
        voice.complete(SpeechResult(speech_req.target,sha256(payload).hexdigest(),"en","audio",wav()))
        self.g.revoke(review.identity)
        for result in (turn.result,voice.result):
            with self.assertRaises(Refused):
                result()
    def test_revoked_review_before_submit(self):
        req=self.stt.prepare_local_turn(self.c,"turn","en",native_completion=True)
        grant=self.g.fixture_grant("turn",req,60)
        review=self.g.fixture_grant("review",self.stt.prepare_review(self.c,"turn","en",native_completion=True),60)
        self.g.revoke(review.identity)
        with self.assertRaises(Refused):
            self.stt.start_local_turn(self.c,"turn","en",grant,native_completion=True,review_grant=review)
