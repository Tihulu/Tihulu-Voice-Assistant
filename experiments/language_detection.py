# SPDX-License-Identifier: AGPL-3.0-only
"""Typed, fixed Whisper fixture language detection. Not a mixed-language detector."""
from dataclasses import dataclass
from threading import Lock
import re
from gateway import Refused,FixtureGateway
from capabilities import CapabilityCall

MIN_CONFIDENCE_PPM=500000
_LINE=re.compile(rb"whisper_full_with_state: auto-detected language: ([a-z]{2,3}) \(p = ([01])\.([0-9]{6})\)\Z")

def parse_detection(raw):
    if type(raw) is not bytes or not 0<len(raw)<=65536:
        raise Refused("language_detection_log_limit")
    candidates=[line for line in raw.splitlines() if b"auto-detected language:" in line]
    if len(candidates)!=1:
        raise Refused("language_detection_ambiguous")
    match=_LINE.fullmatch(candidates[0])
    if not match:
        raise Refused("language_detection_invalid")
    lang=match[1].decode("ascii")
    ppm=int(match[2])*1000000+int(match[3])
    if lang not in ("tr","en","de") or not MIN_CONFIDENCE_PPM<=ppm<=1000000:
        raise Refused("language_detection_hold")
    return lang,ppm

@dataclass(frozen=True)
class Detection:
    target: str
    source_digest: str
    model_digest: str
    output: str
    language: str
    confidence_ppm: int

class DetectionFixture:
    def __init__(self,gateway,context,call,grant,*,dependencies=()):
        if type(gateway) is not FixtureGateway or type(call) is not CapabilityCall or (call.adapter,call.operation)!=("whisper_cpp","detect_language"):
            raise Refused("language_detection_fixture")
        self._g,self._grant=gateway,grant
        self._req=call.request(context)
        self._args=dict(call.args)
        self._version=call.version
        if type(dependencies) is not tuple:
            raise Refused("invalid_detection_dependencies")
        if dependencies:
            gateway.claim_with_dependencies(grant,self._req,dependencies)
        else:
            gateway.claim(grant,self._req)
        self._lock=Lock()
        self._state="ready"
        self._result=None

    def checkpoint(self):
        with self._lock:
            if self._state!="ready":
                raise Refused("language_detection_terminal")
            self._g.checkpoint(self._grant,self._req)

    def complete(self,value):
        with self._lock:
            if self._state!="ready":
                raise Refused("language_detection_terminal")
            try:
                self._g.checkpoint(self._grant,self._req)
                if (type(value) is not Detection or value.target!=self._req.target
                        or value.source_digest!=self._args["digest"] or value.model_digest!=self._args["model_digest"]
                        or value.output!=self._args["output"] or value.language not in ("tr","en","de")
                        or type(value.confidence_ppm) is not int or not MIN_CONFIDENCE_PPM<=value.confidence_ppm<=1000000):
                    raise Refused("language_detection_provenance")
                self._g.checkpoint(self._grant,self._req)
                self._result=value
                self._state="completed"
            except Refused:
                self._result=None
                self._state="held"
                raise

    def _completed(self):
        if self._state!="completed" or self._result is None:
            raise Refused("language_detection_no_result")
        try:
            self._g.checkpoint(self._grant,self._req)
        except Refused:
            self._state="held"
            self._result=None
            raise
        return self._result

    def result(self):
        with self._lock:
            return self._completed()

    def _transcription(self,output):
        value=self._completed()
        if output in (self._args["artifact"],self._args["output"]):
            raise Refused("language_detection_output_alias")
        return CapabilityCall("whisper_cpp",self._version,"transcribe_wav",
                 (("artifact",self._args["artifact"]),("digest",value.source_digest),
                  ("model_digest",value.model_digest),("language",value.language),("output",output))).checked()

    def prepare_transcription(self,output):
        with self._lock:
            return self._transcription(output)

    def start_transcription(self,output,grant):
        from transcription_result import TranscriptFixture
        with self._lock:
            return TranscriptFixture(self._g,self._req.context,self._transcription(output),grant,
                                     dependencies=((self._grant,self._req),))

    def cancel(self):
        with self._lock:
            self._result=None
            self._state="cancelled"
            self._g.revoke(self._grant.identity)
