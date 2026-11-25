# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory release lifecycle rehearsal. Never installs, launches or writes files."""
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from gateway import Context, FixtureGateway, Request, Refused, token

MAX_FILES=64
MAX_BYTES=262144
SLOTS=("launcher","modules","desktop","icon","license")
def digest(data):
    return sha256(data).hexdigest()

def key(*parts):
    return digest(json.dumps(parts,ensure_ascii=True,separators=(",",":")).encode("ascii"))

def hex_id(s,size):
    if type(s) is not str or len(s)!=size or any(c not in "0123456789abcdef" for c in s):
        raise Refused("invalid_provenance")
    return s

@dataclass(frozen=True)
class Package:
    commit: str
    tree: str
    version: str
    files: tuple

    def checked(self):
        hex_id(self.commit,40)
        hex_id(self.tree,40)
        token(self.version)
        if (type(self.files) is not tuple or len(self.files)!=len(SLOTS)
                or any(type(p) is not tuple or len(p)!=2 for p in self.files)):
            raise Refused("invalid_package")
        if tuple(p[0] for p in self.files)!=SLOTS:
            raise Refused("invalid_package_slots")
        if any(type(p[1]) is not bytes or not 0<len(p[1])<=MAX_BYTES for p in self.files):
            raise Refused("invalid_package_bytes")
        if sum(len(p[1]) for p in self.files)>MAX_BYTES:
            raise Refused("package_limit")
        return self

    @property
    def identity(self):
        self.checked()
        return key("package",self.commit,self.tree,self.version,
                   tuple((name,digest(data)) for name,data in self.files))

@dataclass(frozen=True)
class Change:
    scope: str
    generation: int
    operation: str
    previous: str
    next: str
    retained: str

    @property
    def target(self):
        token(self.scope)
        if type(self.generation) is not int or not 0<=self.generation<2**63:
            raise Refused("invalid_generation")
        if self.operation not in ("install","upgrade","rollback","uninstall"):
            raise Refused("invalid_operation")
        for value in (self.previous,self.next,self.retained):
            if value:
                hex_id(value,64)
            elif type(value) is not str:
                raise Refused("invalid_identity")
        return key("release-change",self.scope,self.generation,self.operation,
                   self.previous,self.next,self.retained)

def rehearsal_target(identity,retained):
    return key("release-rehearsal",token(identity),retained_digest(retained))

def retained_digest(retained):
    if (type(retained) is not tuple or len(retained)>MAX_FILES
            or any(type(p) is not tuple or len(p)!=2 for p in retained)):
        raise Refused("invalid_retained")
    for name,data in retained:
        token(name)
        if not name.startswith(("user-","shared-","stable-")) or type(data) is not bytes:
            raise Refused("invalid_retained")
    if len({p[0] for p in retained})!=len(retained) or sum(len(p[1]) for p in retained)>MAX_BYTES:
        raise Refused("retained_limit")
    return key("retained",tuple(sorted((name,digest(data)) for name,data in retained)))

class Rehearsal:
    """Symbolic slots only. A failed transition cannot alter current package."""
    def __init__(self,gateway,context,identity,retained,grant):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context!=context or context.mode=="CLOUD_CHAT"):
            raise Refused("local_context_required")
        req=Request(context,"publish",rehearsal_target(identity,retained),"local")
        gateway.claim(grant,req)
        self._g,self._ctx=gateway,context
        self._root=(grant,req)
        self._scope=key("rehearsal-instance",identity,grant.identity)
        self._retained=retained
        self._retained_digest=retained_digest(retained)
        self._packages={}
        self._current=""
        self._rollback=""
        self._generation=0
        self._closed=False
        self._lock=RLock()
        try:
            self._live()
        except BaseException:
            self.close()
            raise

    def _live(self):
        if self._closed:
            raise Refused("rehearsal_closed")
        self._g.checkpoint(*self._root)
        if retained_digest(self._retained)!=self._retained_digest:
            raise Refused("retained_data_changed")

    def register(self,package,expected_identity):
        with self._lock:
            self._live()
            if type(package) is not Package or package.identity!=expected_identity:
                raise Refused("package_identity_mismatch")
            if package.identity not in self._packages and len(self._packages)>=8:
                raise Refused("package_limit")
            if any(p.version==package.version and p.identity!=package.identity for p in self._packages.values()):
                raise Refused("version_conflict")
            self._packages[package.identity]=package
            return package.identity

    def plan(self,operation,next_identity=""):
        with self._lock:
            self._live()
            if type(next_identity) is not str:
                raise Refused("invalid_identity")
            if operation=="uninstall":
                valid=bool(self._current) and not next_identity
            elif operation=="install":
                valid=not self._current and next_identity in self._packages
            elif operation=="upgrade":
                valid=bool(self._current) and next_identity in self._packages and next_identity!=self._current
            elif operation=="rollback":
                valid=bool(self._current) and bool(self._rollback) and next_identity==self._rollback
            else:
                valid=False
            if not valid:
                raise Refused("invalid_transition")
            change=Change(self._scope,self._generation,operation,self._current,next_identity,self._retained_digest)
            change.target
            return change

    def apply(self,change,grant,*,confirmed=False,fixture_runtime_ok=False):
        with self._lock:
            self._live()
            if confirmed is not True or type(change) is not Change or type(fixture_runtime_ok) is not bool:
                raise Refused("explicit_change_required")
            if self.plan(change.operation,change.next)!=change:
                raise Refused("stale_change")
            req=Request(self._ctx,"publish",change.target,"local")
            self._g.claim(grant,req)
            try:
                self._live()
                self._g.checkpoint(grant,req)
                # A supplied false smoke result models failure before commit.
                if change.operation!="uninstall" and fixture_runtime_ok is not True:
                    raise Refused("fixture_runtime_failed")
                if change.next and self._packages[change.next].identity!=change.next:
                    raise Refused("package_changed")
                self._live()
                self._g.checkpoint(grant,req)
                if self.plan(change.operation,change.next)!=change:
                    raise Refused("stale_change")
                self._rollback=self._current if change.operation in ("upgrade","rollback") else ""
                self._current=change.next
                self._generation+=1
                return self.snapshot()
            except BaseException:
                self._g.revoke(grant.identity)
                raise

    def snapshot(self):
        with self._lock:
            self._live()
            package=self._packages.get(self._current)
            return (self._generation,self._current,self._rollback,
                    package.files if package is not None else (),self._retained)

    def close(self):
        with self._lock:
            self._closed=True
            self._packages.clear()
            self._current=self._rollback=""
            self._retained=()
            self._g.revoke(self._root[0].identity)
