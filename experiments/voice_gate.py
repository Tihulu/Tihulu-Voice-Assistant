# SPDX-License-Identifier: AGPL-3.0-only
"""Opt-in offline VAD experiment on supplied PCM frames only.
No microphone, wake-word detector, STT, TTS or auto-submission.
"""
from dataclasses import dataclass
from hashlib import sha256
from struct import unpack
from threading import Lock
from gateway import Request,Refused,token

SAMPLES=320
FRAME_BYTES=640
MAX_FRAMES=500

@dataclass(frozen=True)
class Config:
    device: str
    threshold: int=600
    start_frames: int=3
    end_frames: int=12
    max_frames: int=MAX_FRAMES
    def target(self):
        token(self.device)
        if (type(self.threshold) is not int or not 1<=self.threshold<=32767
                or type(self.start_frames) is not int or not 1<=self.start_frames<=10
                or type(self.end_frames) is not int or not 1<=self.end_frames<=50
                or type(self.max_frames) is not int
                or not self.start_frames+self.end_frames<=self.max_frames<=MAX_FRAMES):
            raise Refused("invalid_vad_config")
        return sha256(repr(("vad-pcm16-16000",self.device,self.threshold,self.start_frames,
                            self.end_frames,self.max_frames)).encode("ascii")).hexdigest()

class VoiceGate:
    """One explicit arm, one utterance; no automatically repeated capture."""
    def __init__(self,gateway,context,config):
        if type(config) is not Config:
            raise Refused("invalid_vad_config")
        self._g,self._ctx,self._cfg=gateway,context.checked(),config
        self._req=Request(context,"engine",config.target(),"local","private")
        self._lock=Lock()
        self._state="off"
        self._grant=None
        self._frames=[]
        self._lead=[]
        self._silence=0
        self._seen=0
    @property
    def state(self):
        with self._lock:
            return self._state
    def arm(self,grant):
        with self._lock:
            if self._state!="off":
                raise Refused("not_off")
            self._g.claim(grant,self._req)
            self._grant=grant
            self._state="listening"
    def _discard(self,state):
        self._frames.clear()
        self._lead.clear()
        self._state=state
        if self._grant is not None:
            self._g.revoke(self._grant.identity)
    def feed(self,pcm):
        with self._lock:
            if self._state not in ("listening","speech"):
                raise Refused("not_listening")
            try:
                self._g.checkpoint(self._grant,self._req)
                if type(pcm) is not bytes or len(pcm)!=FRAME_BYTES:
                    raise Refused("invalid_pcm")
                self._seen+=1
                voiced=sum(x*x for x in unpack("<320h",pcm))>=SAMPLES*self._cfg.threshold**2
                if self._state=="listening":
                    if voiced:
                        self._lead.append(pcm)
                        if len(self._lead)==self._cfg.start_frames:
                            self._frames.extend(self._lead)
                            self._lead.clear()
                            self._state="speech"
                    else:
                        self._lead.clear()
                else:
                    self._frames.append(pcm)
                    self._silence=0 if voiced else self._silence+1
                    if self._silence>=self._cfg.end_frames:
                        del self._frames[-self._silence:]
                        self._state="review"
                if self._seen>=self._cfg.max_frames and self._state!="review":
                    self._discard("timeout")
                if self._state in ("listening","speech","review"):
                    self._g.checkpoint(self._grant,self._req)
                return self._state
            except Refused:
                self._discard("held")
                raise
    def take(self):
        """Explicit review action returns PCM once, never a submitted turn."""
        with self._lock:
            if self._state!="review":
                raise Refused("no_review")
            try:
                self._g.checkpoint(self._grant,self._req)
                data=b"".join(self._frames)
                self._g.checkpoint(self._grant,self._req)
            except Refused:
                self._discard("held")
                raise
            self._discard("completed")
            return data
    def stop(self):
        with self._lock:
            self._discard("stopped")
