# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-reviewed model revision pointer experiment; no disk or download."""
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from gateway import Request, Refused, token
from market_fixture import Manifest

@dataclass(frozen=True)
class Change:
    model: str
    previous: str
    next: str
    generation: int
    scope: str

    def target(self):
        token(self.model)
        token(self.scope)
        for v in (self.previous,self.next):
            if not isinstance(v,str) or len(v) not in (0,64) or any(c not in "0123456789abcdef" for c in v):
                raise Refused("invalid_revision")
        if not self.next or self.previous==self.next or type(self.generation) is not int or not 0<=self.generation<2**63:
            raise Refused("invalid_change")
        return sha256(repr((self.scope,self.model,self.previous,self.next,self.generation)).encode("ascii")).hexdigest()

def root_target(ctx):
    return sha256(repr(("version-book-root",ctx.checked())).encode("ascii")).hexdigest()

class VersionBook:
    """Retains immutable verified fixture manifests, not installed weights.

    Pointer changes use compare-and-swap generation, exact request consent and
    explicit previous revision. Rollback is a new change, never an implicit fallback.
    """
    def __init__(self,gateway,context,root):
        ctx=context.checked()
        req=Request(ctx,"publish",root_target(ctx),"local")
        gateway.claim(root,req)
        self._g,self._ctx=gateway,ctx
        self._root,self._req=root,req
        self._scope=sha256(repr(("version-book",root.identity)).encode("ascii")).hexdigest()
        self._items={}
        self._current={}
        self._gen={}
        self._lock=RLock()
    def _live(self):
        self._g.checkpoint(self._root,self._req)
    def record(self,manifest,data):
        if type(manifest) is not Manifest:
            raise Refused("invalid_manifest")
        manifest.checked()
        if (type(data) is not bytes or len(data)!=manifest.size
                or sha256(data).hexdigest()!=manifest.sha256):
            raise Refused("integrity_mismatch")
        with self._lock:
            self._live()
            for item,_ in self._items.values():
                if ((item.model,item.revision,item.file)==(manifest.model,manifest.revision,manifest.file)
                        and item.identity!=manifest.identity):
                    raise Refused("revision_identity_conflict")
            if manifest.identity not in self._items and len(self._items)>=64:
                raise Refused("version_limit")
            self._live()
            self._items[manifest.identity]=(manifest,data)
            return manifest.identity
    def plan(self,model,next_identity):
        token(model)
        token(next_identity)
        with self._lock:
            self._live()
            record=self._items.get(next_identity)
            if record is None or record[0].model!=model:
                raise Refused("revision_not_verified")
            c=Change(model,self._current.get(model,""),next_identity,self._gen.get(model,0),self._scope)
            c.target()
            return c
    def apply(self,change,grant):
        if type(change) is not Change:
            raise Refused("invalid_change")
        req=Request(self._ctx,"publish",change.target(),"local")
        with self._lock:
            self._g.checkpoint(grant,req)
            if self.plan(change.model,change.next)!=change:
                raise Refused("stale_change")
            self._g.claim(grant,req)
            try:
                self._live()
                self._g.checkpoint(grant,req)
                if self.plan(change.model,change.next)!=change:
                    raise Refused("stale_change")
                current=dict(self._current)
                generation=dict(self._gen)
                current[change.model]=change.next
                generation[change.model]=change.generation+1
                self._g.checkpoint(grant,req)
                self._live()
                self._current,self._gen=current,generation
                return "FIXTURE_POINTER_ONLY"
            finally:
                self._g.revoke(grant.identity)
    def current(self,model):
        with self._lock:
            self._live()
            return self._current.get(model)

    def current_bytes(self,model):
        with self._lock:
            identity=self.current(model)
            return None if identity is None else self._items[identity][1]

    def close(self):
        with self._lock:
            self._g.revoke(self._root.identity)
            self._items.clear()
            self._current.clear()
            self._gen.clear()
