# SPDX-License-Identifier: AGPL-3.0-only
"""Private image/frame receipt and local visual review fixture. No actual capture or inference."""
from dataclasses import dataclass
from hashlib import sha256
from threading import current_thread
import json
from gateway import Context,FixtureGateway,Request,Refused,token
from vision_image_input import decode_png

def target(*fields):
    return sha256(json.dumps(fields,separators=(",",":"),ensure_ascii=True).encode("ascii")).hexdigest()

def identity(value):
    if type(value) is not str:
        raise Refused("vision_identity")
    return token(value)

@dataclass(frozen=True,eq=False)
class Frame:
    surface: str
    epoch: int
    width: int
    height: int
    channels: int
    source_digest: str
    pixel_digest: str

class PrivateFrames:
    """Single selected image or window. Every change invalidates its observations.
    Trusted caller provides bytes; no capture, file access or secret detection.
    """
    def __init__(self,gateway,surface,source):
        if type(gateway) is not FixtureGateway or type(source) is not str or source not in ("image","window"):
            raise Refused("vision_scope")
        self._g=gateway
        self._surface=identity(surface)
        self._source=source
        self._owner=current_thread()
        self._frame=None
        self._raster=None
        self._epoch=0
        self._closed=False
        self._reviews={}

    def _check(self):
        if current_thread() is not self._owner or self._closed:
            raise Refused("vision_session_closed")

    def clear(self):
        self._check()
        for grant,req in self._reviews.values():
            self._g.revoke(grant.identity)
        self._reviews.clear()
        self._frame=self._raster=None

    def replace(self,raw,*,sensitive=False):
        self._check()
        self.clear()
        if type(sensitive) is not bool or sensitive or self._epoch>=64:
            raise Refused("vision_sensitive_or_limit")
        raster=decode_png(raw)
        self._epoch+=1
        self._frame=Frame(self._surface,self._epoch,raster.width,raster.height,raster.channels,
                          raster.source_digest,raster.pixel_digest)
        self._raster=raster
        return self._frame

    def current(self,frame):
        self._check()
        if frame is None or frame is not self._frame:
            raise Refused("vision_stale_frame")
        return frame

    def request(self,frame,agent,model):
        self.current(frame)
        # Distinct target for this exact private frame, role and model.
        t=target("vision-local-review-v1",self._source,frame.surface,frame.epoch,
                 frame.source_digest,frame.pixel_digest,identity(agent),identity(model))
        return Request(self._g.context,"document_engine",t,"local","private").checked()

    def review(self,frame,agent,model,grant):
        req=self.request(frame,agent,model)
        if agent in self._reviews or len(self._reviews)>=10:
            raise Refused("vision_agent_limit")
        self._g._validate_grant(grant,req,claim=True)
        self._reviews[agent]=(grant,req)
        return self._raster.pixels

    def checkpoint(self,frame,agent):
        self.current(frame)
        pair=self._reviews.get(identity(agent))
        if pair is None:
            raise Refused("vision_review_required")
        self._g.checkpoint_claimed_group((pair,))
        return pair

    def close(self):
        if current_thread() is not self._owner:
            raise Refused("vision_session_thread")
        if not self._closed:
            self.clear()
            self._closed=True
