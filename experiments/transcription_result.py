# SPDX-License-Identifier: AGPL-3.0-only
"""Private typed transcription receipt for fixed local fixtures."""
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import Lock
from gateway import FixtureGateway, Refused
from capabilities import CapabilityCall
from adapter_protocol import text_bytes

@dataclass(frozen=True)
class Transcript:
    target: str
    source_digest: str
    model_digest: str
    language: str
    output: str
    text: str

class TranscriptFixture:
    def __init__(self,gateway,context,call,grant):
        if type(gateway) is not FixtureGateway or type(call) is not CapabilityCall or (call.adapter,call.operation)!=("whisper_cpp","transcribe_wav"):
            raise Refused("transcript_fixture")
        self._g,self._grant=gateway,grant
        self._req=call.request(context)
        self._args=dict(call.args)
        gateway.claim(grant,self._req)
        self._state="ready"
        self._result=None
        self._lock=Lock()

    def checkpoint(self):
        with self._lock:
            if self._state!="ready":
                raise Refused("transcript_terminal")
            self._g.checkpoint(self._grant,self._req)

    def complete(self,value):
        with self._lock:
            if self._state!="ready":
                raise Refused("transcript_terminal")
            try:
                self._g.checkpoint(self._grant,self._req)
                if (type(value) is not Transcript or value.target!=self._req.target
                        or value.source_digest!=self._args["digest"] or value.model_digest!=self._args["model_digest"]
                        or value.language!=self._args["language"] or value.output!=self._args["output"]):
                    raise Refused("transcript_provenance")
                data=text_bytes(value.text)
                if not value.text.strip():
                    raise Refused("transcript_empty")
                self._g.checkpoint(self._grant,self._req)
                self._result=(value,sha256(data).hexdigest())
                self._state="completed"
            except Refused:
                self._result=None
                self._state="held"
                raise

    def result(self):
        with self._lock:
            if self._state!="completed":
                raise Refused("transcript_no_result")
            try:
                self._g.checkpoint(self._grant,self._req)
            except Refused:
                self._result=None
                self._state="held"
                raise
            return self._result

    def _turn_input(self,candidate,identity,response_language):
        from inference import Candidate
        from adapter_protocol import prepare,local_request
        if self._state!="completed" or self._result is None:
            raise Refused("transcript_no_result")
        self._g.checkpoint(self._grant,self._req)
        if type(candidate) is not Candidate or candidate.locality!="local" or candidate.task!="llm":
            raise Refused("local_llm_required")
        if type(response_language) is not str or response_language not in ("tr","en","de"):
            raise Refused("invalid_response_language")
        value,result_digest=self._result
        payload=json.dumps({"kind":"voice_turn","artifact":value.output,
                   "source_digest":value.source_digest,"model_digest":value.model_digest,
                   "transcript_digest":result_digest,"input_language":value.language,
                   "response_language":response_language,"transcript":value.text},
                   ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
        call=prepare(candidate,identity,"llm",payload)
        return local_request(self._req.context,call,dependent=True),payload

    def prepare_local_turn(self,candidate,identity,response_language):
        with self._lock:
            req,_=self._turn_input(candidate,identity,response_language)
            return req

    def start_local_turn(self,candidate,identity,response_language,grant):
        from adapter_protocol import LocalAdapter
        with self._lock:
            _,payload=self._turn_input(candidate,identity,response_language)
            job=LocalAdapter(self._g,self._req.context,candidate,identity,"llm",
                             payload,grant,dependencies=((self._grant,self._req),),
                             response_language=response_language)
            return job,payload

    def cancel(self):
        with self._lock:
            self._state="cancelled"
            self._result=None
            self._g.revoke(self._grant.identity)
