# SPDX-License-Identifier: AGPL-3.0-only
"""Pure sequential workflow experiment. No execution or I/O authority."""
from dataclasses import dataclass
from gateway import Request, Refused, token
from hashlib import sha256
from threading import RLock

MAX_BYTES = 65536

@dataclass(frozen=True)
class Step:
    identity: str
    engine: str
    input_kind: str
    output_kind: str

@dataclass(frozen=True)
class Artifact:
    kind: str
    digest: str
    parent: str

def step_target(step, artifact):
    if type(step) is not Step or type(artifact) is not Artifact:
        raise Refused("invalid_step_artifact")
    for x in (step.identity, step.engine, step.input_kind, step.output_kind, artifact.kind):
        token(x)
    if (artifact.kind != step.input_kind or not isinstance(artifact.digest,str)
            or len(artifact.digest)!=64 or any(c not in "0123456789abcdef" for c in artifact.digest)):
        raise Refused("invalid_step_artifact")
    return sha256(repr((step.identity,step.engine,step.input_kind,step.output_kind,
                        artifact.digest)).encode("ascii")).hexdigest()

class Chain:
    def __init__(self, gateway, context, steps, initial_kind, initial_data):
        if type(steps) is not tuple or not 1 <= len(steps) <= 16:
            raise Refused("invalid_chain")
        seen = set()
        for i, step in enumerate(steps):
            if type(step) is not Step:
                raise Refused("invalid_step")
            for x in (step.identity,step.engine,step.input_kind,step.output_kind):
                token(x)
            if step.identity in seen or i and steps[i-1].output_kind != step.input_kind:
                raise Refused("incompatible_chain")
            seen.add(step.identity)
        if (initial_kind != steps[0].input_kind or type(initial_data) is not bytes
                or not 0 < len(initial_data) <= MAX_BYTES):
            raise Refused("invalid_initial_artifact")
        self._g,self._ctx,self._steps = gateway,context,steps
        self._index,self._active = 0,None
        self._current = Artifact(initial_kind,sha256(initial_data).hexdigest(),"")
        self._data = initial_data
        self._state = "ready"
        self._checks = []
        self._lock = RLock()

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def current(self):
        with self._lock:
            if self._state not in ("ready","running","completed"):
                raise Refused("no_current_artifact")
            self._check_lineage()
            return self._current

    def target(self, artifact):
        with self._lock:
            if self._state != "ready" or artifact is not self._current:
                raise Refused("stale_input_artifact")
            self._check_lineage()
            return step_target(self._steps[self._index],artifact)

    def begin(self, grant, artifact):
        with self._lock:
            req=Request(self._ctx,"engine",self.target(artifact),"local")
            self._g.claim(grant,req)
            self._g.checkpoint(grant,req)
            handle=object()
            self._active=(handle,grant,req,artifact)
            self._state="running"
            return handle

    def _owned(self,handle):
        if self._active is None or handle is not self._active[0]:
            raise Refused("not_owned")
        return self._active

    def _hold(self):
        if self._active is not None:
            self._g.revoke(self._active[1].identity)
        self._checks.clear()
        self._state,self._active,self._current,self._data="held",None,None,b""

    def _check_lineage(self):
        try:
            for grant,req in self._checks:
                self._g.checkpoint(grant,req)
        except Refused:
            self._hold()
            raise

    def input_bytes(self,handle):
        with self._lock:
            return self._input_bytes(handle)

    def _input_bytes(self,handle):
        _,grant,req,artifact=self._owned(handle)
        try:
            self._check_lineage()
            self._g.checkpoint(grant,req)
            if artifact is not self._current or sha256(self._data).hexdigest()!=artifact.digest:
                raise Refused("input_digest_mismatch")
            return self._data
        except Refused:
            self._hold()
            raise

    def complete(self,handle,kind,data):
        with self._lock:
            return self._complete(handle,kind,data)

    def _complete(self,handle,kind,data):
        _,grant,req,artifact=self._owned(handle)
        step=self._steps[self._index]
        try:
            self.input_bytes(handle)
            if kind!=step.output_kind or type(data) is not bytes or not 0<len(data)<=MAX_BYTES:
                raise Refused("invalid_artifact")
            result=Artifact(kind,sha256(data).hexdigest(),artifact.digest)
            self._g.checkpoint(grant,req)
            self._check_lineage()
        except Refused:
            self._hold()
            raise
        self._checks.append((grant,req))
        self._current,self._data=result,data
        self._index+=1
        self._active=None
        self._state="completed" if self._index==len(self._steps) else "ready"
        if self._state=="completed":
            self._data=b""
        return result

    def cancel(self):
        with self._lock:
            if self._state in ("ready","running"):
                if self._active is not None:
                    self._g.revoke(self._active[1].identity)
                self._checks.clear()
                self._state,self._active,self._current,self._data="cancelled",None,None,b""
