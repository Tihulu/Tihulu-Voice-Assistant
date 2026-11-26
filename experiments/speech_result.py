# SPDX-License-Identifier: AGPL-3.0-only
"""Closed PCM result contract for synthetic local speech fixtures only."""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
import struct
from gateway import FixtureGateway, Refused
from capabilities import CapabilityCall

MAX_WAV=1024*1024

def pcm_info(data):
    if type(data) is not bytes or not 44<len(data)<=MAX_WAV:
        raise Refused("speech_size")
    if data[:4]!=b"RIFF" or data[8:12]!=b"WAVE" or int.from_bytes(data[4:8],"little")!=len(data)-8:
        raise Refused("speech_riff")
    # Closed, canonical PCM fixture: one fmt and one data chunk, no hidden
    # chunks or trailing bytes. eSpeak emits mono 16-bit PCM.
    if data[12:16]!=b"fmt " or data[16:20]!=b"\x10\x00\x00\x00" or data[36:40]!=b"data":
        raise Refused("speech_chunks")
    fmt,channels,rate,byte_rate,align,bits=struct.unpack_from("<HHIIHH",data,20)
    size=int.from_bytes(data[40:44],"little")
    if (fmt!=1 or channels!=1 or rate not in (16000,22050,24000,44100,48000)
            or bits!=16 or align!=2 or byte_rate!=rate*2 or size!=len(data)-44
            or size%2 or not rate//5<=size//2<=rate*20):
        raise Refused("speech_pcm")
    samples=memoryview(data)[44:]
    if not any(samples):
        raise Refused("speech_silent")
    return (rate,size//2)

@dataclass(frozen=True)
class SpeechResult:
    target: str
    source_digest: str
    language: str
    output: str
    data: bytes

class SpeechFixture:
    def __init__(self,gateway,context,call,grant):
        if type(gateway) is not FixtureGateway or type(call) is not CapabilityCall or (call.adapter,call.operation)!=("espeak_ng","text_to_wav"):
            raise Refused("speech_fixture")
        self._g,self._grant=gateway,grant
        self._req=call.request(context)
        self._args=dict(call.args)
        gateway.claim(grant,self._req)
        self._lock=Lock()
        self._state="ready"
        self._receipt=None

    def checkpoint(self):
        with self._lock:
            if self._state!="ready":
                raise Refused("speech_terminal")
            self._g.checkpoint(self._grant,self._req)

    def complete(self,result):
        with self._lock:
            if self._state!="ready":
                raise Refused("speech_terminal")
            try:
                self._g.checkpoint(self._grant,self._req)
                if (type(result) is not SpeechResult or result.target!=self._req.target
                        or result.source_digest!=self._args["digest"]
                        or result.language!=self._args["language"] or result.output!=self._args["output"]):
                    raise Refused("speech_provenance")
                rate,count=pcm_info(result.data)
                self._g.checkpoint(self._grant,self._req)
                self._receipt=(self._req.target,result.source_digest,result.language,result.output,
                               sha256(result.data).hexdigest(),rate,count)
                self._state="completed"
            except Refused:
                self._receipt=None
                self._state="held"
                raise

    def result(self):
        with self._lock:
            if self._state!="completed":
                raise Refused("speech_no_result")
            try:
                self._g.checkpoint(self._grant,self._req)
            except Refused:
                self._receipt=None
                self._state="held"
                raise
            return self._receipt

    def cancel(self):
        with self._lock:
            self._receipt=None
            self._state="cancelled"
            self._g.revoke(self._grant.identity)
