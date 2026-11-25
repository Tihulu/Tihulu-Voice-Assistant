# SPDX-License-Identifier: AGPL-3.0-only
"""Reviewed PCM to text fixture. No microphone, STT process or chat send."""
from io import BytesIO
import wave
from hashlib import sha256
from dataclasses import dataclass
from gateway import Request,Refused
from adapter_protocol import prepare,LocalAdapter
from voice_gate import FRAME_BYTES,MAX_FRAMES

@dataclass(frozen=True)
class Draft:
    pcm_digest: str
    text: str

def wav_bytes(pcm):
    if type(pcm) is not bytes or not 0<len(pcm)<=FRAME_BYTES*MAX_FRAMES or len(pcm)%FRAME_BYTES:
        raise Refused("invalid_pcm")
    out=BytesIO()
    with wave.open(out,"wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return out.getvalue()

def target(candidate,identity,pcm):
    # The normalized local adapter has a smaller request limit than the VAD.
    # Bind the whole WAV's digest instead of passing sample bytes to a model.
    raw=wav_bytes(pcm)
    return prepare(candidate,identity,"stt",sha256(raw).digest()).target

def review(gateway,context,candidate,identity,pcm,grant,events):
    if type(events) is not tuple or not 1<=len(events)<=128:
        raise Refused("invalid_events")
    raw=wav_bytes(pcm)
    a=LocalAdapter(gateway,context,candidate,identity,"stt",sha256(raw).digest(),grant)
    try:
        for n,event in enumerate(events,1):
            if type(event) is not tuple or len(event)!=2:
                raise Refused("invalid_event")
            a.event(identity,n,event[0],event[1])
        result=a.result().decode("utf-8")
        if not result.strip():
            raise Refused("empty_transcript")
        return Draft(sha256(pcm).hexdigest(),result)
    except BaseException:
        a.cancel()
        raise
