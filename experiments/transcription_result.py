# SPDX-License-Identifier: AGPL-3.0-only
"""Private typed transcription receipt for fixed local fixtures."""
from dataclasses import dataclass
from hashlib import sha256
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

    def cancel(self):
        with self._lock:
            self._state="cancelled"
            self._result=None
            self._g.revoke(self._grant.identity)
