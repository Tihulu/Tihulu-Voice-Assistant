# SPDX-License-Identifier: AGPL-3.0-only
"""Normalized agent connector fixture; not OpenCode/ACP wire compatibility."""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from gateway import Request, Refused, token

@dataclass(frozen=True)
class JobSpec:
    adapter: str
    session: str
    job: str
    capability: str
    payload_digest: str

    def target(self):
        for v in (self.adapter,self.session,self.job,self.capability):
            token(v)
        if self.capability not in ("plan","local-analysis"):
            raise Refused("unsupported_capability")
        if (type(self.payload_digest) is not str or len(self.payload_digest)!=64
                or any(c not in "0123456789abcdef" for c in self.payload_digest)):
            raise Refused("invalid_payload_digest")
        return sha256(repr((self.adapter,self.session,self.job,self.capability,
                            self.payload_digest)).encode("ascii")).hexdigest()

class Connector:
    """Single owner session. Only typed fixture events; never executes tools."""
    def __init__(self,gateway,context,adapter,session):
        self._g,self._ctx=gateway,context.checked()
        self._adapter,self._session=token(adapter),token(session)
        self._active=None
        self._seen=set()
        self._closed=False
        self._status="idle"
        self._lock=Lock()

    @property
    def status(self):
        with self._lock:
            return self._status

    def begin(self,spec,grant,payload):
        with self._lock:
            return self._begin(spec,grant,payload)

    def _begin(self,spec,grant,payload):
        if type(spec) is not JobSpec:
            raise Refused("invalid_job")
        target=spec.target()
        if (self._closed or self._active is not None or type(spec) is not JobSpec
                or spec.adapter!=self._adapter or spec.session!=self._session
                or type(payload) is not bytes or not 0<len(payload)<=16384
                or sha256(payload).hexdigest()!=spec.payload_digest
                or spec.job in self._seen or len(self._seen)>=64):
            raise Refused("invalid_job")
        req=Request(self._ctx,"agent",target,"local")
        self._g.claim(grant,req)
        handle=object()
        self._seen.add(spec.job)
        self._active=(handle,spec,grant,req,0)
        self._status="running"
        return handle

    def event(self,handle,session,job,sequence,kind):
        with self._lock:
            return self._event(handle,session,job,sequence,kind)

    def _event(self,handle,session,job,sequence,kind):
        if self._closed or self._active is None or handle is not self._active[0]:
            raise Refused("not_owned")
        _,spec,grant,req,previous=self._active
        try:
            self._g.checkpoint(grant,req)
            if (session!=spec.session or job!=spec.job or type(sequence) is not int
                    or sequence!=previous+1 or sequence>128
                    or kind not in ("progress","completed","failed","cancelled")):
                raise Refused("invalid_event")
            self._g.checkpoint(grant,req)
        except Refused:
            self._g.revoke(grant.identity)
            self._active=None
            self._status="held"
            raise
        if kind=="progress":
            self._active=(handle,spec,grant,req,sequence)
        else:
            self._active=None
            self._status=kind
        return self._status

    def cancel(self,handle):
        with self._lock:
            if self._active is None or handle is not self._active[0]:
                raise Refused("not_owned")
            self._g.revoke(self._active[2].identity)
            self._active=None
            self._status="cancelled"

    def close(self):
        with self._lock:
            if self._active is not None:
                self._g.revoke(self._active[2].identity)
            self._closed=True
            self._active=None
            self._status="closed"
