# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-reviewed model revision pointer experiment; no disk or download."""
from dataclasses import dataclass
from hashlib import sha256
from gateway import Request, Refused, token
from market_fixture import Manifest

@dataclass(frozen=True)
class Change:
    model: str
    previous: str
    next: str
    generation: int

    def target(self):
        token(self.model)
        for v in (self.previous,self.next):
            if not isinstance(v,str) or len(v) not in (0,64) or any(c not in "0123456789abcdef" for c in v):
                raise Refused("invalid_revision")
        if not self.next or self.previous==self.next or type(self.generation) is not int or not 0<=self.generation<2**63:
            raise Refused("invalid_change")
        return sha256(repr((self.model,self.previous,self.next,self.generation)).encode("ascii")).hexdigest()

class VersionBook:
    """Retains immutable verified fixture manifests, not installed weights.

    Pointer changes use compare-and-swap generation, exact request consent and
    explicit previous revision. Rollback is a new change, never an implicit fallback.
    """
    def __init__(self,gateway,context):
        self._g,self._ctx=gateway,context.checked()
        self._items={}
        self._current={}
        self._gen={}
    def record(self,manifest,data):
        if type(manifest) is not Manifest:
            raise Refused("invalid_manifest")
        manifest.checked()
        if (type(data) is not bytes or len(data)!=manifest.size
                or sha256(data).hexdigest()!=manifest.sha256):
            raise Refused("integrity_mismatch")
        if manifest.identity not in self._items and len(self._items)>=64:
            raise Refused("version_limit")
        self._items[manifest.identity]=manifest
        return manifest.identity
    def plan(self,model,next_identity):
        token(model)
        token(next_identity)
        item=self._items.get(next_identity)
        if item is None or item.model!=model:
            raise Refused("revision_not_verified")
        c=Change(model,self._current.get(model,""),next_identity,self._gen.get(model,0))
        c.target()
        return c
    def apply(self,change,grant):
        if type(change) is not Change:
            raise Refused("invalid_change")
        req=Request(self._ctx,"publish",change.target(),"local")
        self._g.checkpoint(grant,req)
        if self.plan(change.model,change.next)!=change:
            raise Refused("stale_change")
        self._g.checkpoint(grant,req)
        self._current[change.model]=change.next
        self._gen[change.model]=change.generation+1
        return "FIXTURE_POINTER_ONLY"
    def current(self,model):
        return self._current.get(model)
