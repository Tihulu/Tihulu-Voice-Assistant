# SPDX-License-Identifier: AGPL-3.0-only
"""R5 bounded synthetic transfer manifest and versioned commit experiment."""
from dataclasses import dataclass
import hashlib
import re
from threading import Lock
from gateway import Request, Refused, token

MAX_BYTES = 65536
MAX_CHUNK = 4096
HEX = re.compile(r"[0-9a-f]{64}\Z")

@dataclass(frozen=True)
class Manifest:
    model: str
    revision: str
    file: str
    size: int
    sha256: str
    license_id: str

    def checked(self):
        for item in (self.model, self.revision, self.file, self.license_id):
            token(item)
        if (type(self.size) is not int or not 0 < self.size <= MAX_BYTES
                or not isinstance(self.sha256, str) or not HEX.fullmatch(self.sha256)):
            raise Refused("invalid_manifest")
        return self

    @property
    def identity(self):
        return hashlib.sha256(repr((self.model, self.revision, self.file,
                                    self.size, self.sha256, self.license_id)).encode("ascii")).hexdigest()

def registry_target(context):
    return hashlib.sha256(repr(("registry-root",context.checked())).encode("ascii")).hexdigest()

@dataclass(frozen=True)
class Publication:
    scope: str
    model: str
    manifest: str
    previous: str
    generation: int

    def target(self):
        return hashlib.sha256(repr(("registry-publication",self.scope,self.model,
                                    self.manifest,self.previous,self.generation)).encode("ascii")).hexdigest()

class FixtureRegistry:
    """Private immutable synthetic bytes; publication is a distinct owner decision."""
    def __init__(self, gateway, context, root):
        self._g,self._ctx=gateway,context.checked()
        self._req=Request(self._ctx,"publish",registry_target(self._ctx),"local")
        gateway.claim(root,self._req)
        self._root=root
        self._scope=hashlib.sha256(repr(("registry-instance",root.identity)).encode("ascii")).hexdigest()
        self._objects={}
        self._current={}
        self._generation={}
        self._lock=Lock()

    def _live(self):
        self._g.checkpoint(self._root,self._req)

    def current(self,model):
        with self._lock:
            self._live()
            return self._current.get(model)

    def current_bytes(self,model):
        with self._lock:
            self._live()
            record=self._objects.get(self._current.get(model))
            return None if record is None else record[1]

    def _plan(self,manifest):
        manifest.checked()
        return Publication(self._scope,manifest.model,manifest.identity,
                           self._current.get(manifest.model,""),self._generation.get(manifest.model,0))

    def plan(self,manifest):
        with self._lock:
            self._live()
            return self._plan(manifest)

    def _commit(self,transfer,plan,grant):
        """Only called by the owning transfer while its lock is held."""
        if transfer._g is not self._g or transfer._ctx!=self._ctx or type(plan) is not Publication:
            raise Refused("registry_context_mismatch")
        manifest=transfer.manifest
        data=bytes(transfer._data)
        if len(data)!=manifest.size or hashlib.sha256(data).hexdigest()!=manifest.sha256:
            raise Refused("integrity_mismatch")
        request=Request(self._ctx,"publish",plan.target(),"local")
        with self._lock:
            self._live()
            self._g.checkpoint(transfer._grant,transfer._req)
            if plan!=self._plan(manifest):
                raise Refused("stale_publication")
            old=self._objects.get(manifest.identity)
            if old is not None and old!=(manifest,data):
                raise Refused("immutable_conflict")
            for previous,_ in self._objects.values():
                if ((previous.model,previous.revision,previous.file)==(manifest.model,manifest.revision,manifest.file)
                        and previous.identity!=manifest.identity):
                    raise Refused("revision_identity_conflict")
            if old is None and len(self._objects)>=64:
                raise Refused("registry_limit")
            self._g.claim(grant,request)
            try:
                objects=dict(self._objects)
                current=dict(self._current)
                generation=dict(self._generation)
                objects[manifest.identity]=(manifest,data)
                current[manifest.model]=manifest.identity
                generation[manifest.model]=plan.generation+1
                self._live()
                self._g.checkpoint(transfer._grant,transfer._req)
                self._g.checkpoint(grant,request)
                self._objects,self._current,self._generation=objects,current,generation
                return "INTEGRITY_ONLY_NOT_TRUST"
            finally:
                self._g.revoke(grant.identity)

    def close(self):
        with self._lock:
            self._g.revoke(self._root.identity)
            self._objects.clear()
            self._current.clear()
            self._generation.clear()

class FixtureTransfer:
    def __init__(self, gateway, grant, context, manifest):
        self.manifest = manifest.checked()
        self._g, self._grant, self._ctx = gateway, grant, context
        self._req = Request(context, "transfer", manifest.identity, "local", "public")
        self._g.claim(grant,self._req)
        self._lock = Lock()
        self._data = bytearray()
        self._done = False

    def append(self, manifest_id, offset, chunk):
        with self._lock:
            self._g.checkpoint(self._grant,self._req)
            if (self._done or manifest_id!=self.manifest.identity
                    or type(offset) is not int or offset!=len(self._data)
                    or type(chunk) is not bytes or not 0<len(chunk)<=MAX_CHUNK
                    or len(self._data)+len(chunk)>self.manifest.size):
                raise Refused("invalid_chunk")
            self._g.checkpoint(self._grant,self._req)
            self._data.extend(chunk)
            return len(self._data)

    def finish(self, registry, plan, grant):
        with self._lock:
            if self._done:
                raise Refused("already_finished")
            if type(registry) is not FixtureRegistry:
                raise Refused("invalid_registry")
            try:
                return registry._commit(self,plan,grant)
            finally:
                self._done=True
                self._data.clear()
                self._g.revoke(self._grant.identity)

    def cancel(self):
        with self._lock:
            self._done=True
            self._data.clear()
            self._g.revoke(self._grant.identity)
