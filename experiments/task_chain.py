# SPDX-License-Identifier: AGPL-3.0-only
"""Pure sequential workflow experiment, no executor or application control."""
from dataclasses import dataclass
from gateway import Request, Refused, token
from hashlib import sha256

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

class Chain:
    def __init__(self, gateway, context, steps):
        if type(steps) is not tuple or not 1 <= len(steps) <= 16:
            raise Refused("invalid_chain")
        seen = set()
        for i, step in enumerate(steps):
            if type(step) is not Step:
                raise Refused("invalid_step")
            for x in (step.identity, step.engine, step.input_kind, step.output_kind):
                token(x)
            if step.identity in seen or i and steps[i-1].output_kind != step.input_kind:
                raise Refused("incompatible_chain")
            seen.add(step.identity)
        self._g, self._ctx, self._steps = gateway, context, steps
        self._index, self._parent, self._active = 0, "", None
        self._state = "ready"

    @property
    def state(self):
        return self._state

    def begin(self, grant, kind):
        if self._state != "ready":
            raise Refused("chain_not_ready")
        step = self._steps[self._index]
        if kind != step.input_kind:
            raise Refused("input_kind_mismatch")
        req = Request(self._ctx, "engine", step.engine, "local")
        self._g.checkpoint(grant, req)
        handle = object()
        self._active = (handle, grant, req)
        self._state = "running"
        return handle

    def complete(self, handle, kind, data):
        if self._active is None or handle is not self._active[0]:
            raise Refused("not_owned")
        _, grant, req = self._active
        step = self._steps[self._index]
        try:
            self._g.checkpoint(grant, req)
            if kind != step.output_kind or type(data) is not bytes or not 0 < len(data) <= 65536:
                raise Refused("invalid_artifact")
            result = Artifact(kind, sha256(data).hexdigest(), self._parent)
            self._g.checkpoint(grant, req)
        except Refused:
            self._state, self._active = "held", None
            raise
        self._parent = result.digest
        self._index += 1
        self._active = None
        self._state = "completed" if self._index == len(self._steps) else "ready"
        return result

    def cancel(self):
        if self._state in ("ready", "running"):
            self._state, self._active = "cancelled", None
