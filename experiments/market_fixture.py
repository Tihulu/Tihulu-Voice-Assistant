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

class FixtureRegistry:
    """Immutable bytes by manifest; no filesystem operations, actual weights or downloads."""
    def __init__(self):
        self._objects = {}
        self._current = {}

    def current(self, model):
        return self._current.get(model)

    def commit(self, gateway, grant, context, manifest, data):
        manifest.checked()
        req = Request(context, "transfer", manifest.identity, "local", "public")
        gateway.checkpoint(grant, req)
        if (type(data) is not bytes or len(data) != manifest.size
                or hashlib.sha256(data).hexdigest() != manifest.sha256):
            raise Refused("integrity_mismatch")
        old = self._objects.get(manifest.identity)
        if old is not None and old != data:
            raise Refused("immutable_conflict")
        if old is None and len(self._objects) >= 64:
            raise Refused("registry_limit")
        gateway.checkpoint(grant, req)
        self._objects[manifest.identity] = data
        self._current[manifest.model] = manifest.identity
        return "INTEGRITY_ONLY_NOT_TRUST"

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

    def finish(self, registry):
        with self._lock:
            if self._done:
                raise Refused("already_finished")
            if type(registry) is not FixtureRegistry:
                raise Refused("invalid_registry")
            result=registry.commit(self._g,self._grant,self._ctx,self.manifest,bytes(self._data))
            self._done=True
            self._data.clear()
            return result

    def cancel(self):
        with self._lock:
            self._done=True
            self._data.clear()
            self._g.revoke(self._grant.identity)
