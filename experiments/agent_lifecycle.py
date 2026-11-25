# SPDX-License-Identifier: AGPL-3.0-only
"""R4 fixture session ownership; never contacts an agent or starts a process."""
from dataclasses import dataclass
from gateway import Request, Refused, token

@dataclass(frozen=True)
class Handle:
    session: str
    job: str
    context: object
    owner: str

class FixtureAgent:
    def __init__(self, gateway, grant, context, adapter, session, owner):
        self._gateway, self._grant = gateway, grant
        self._req = Request(context, "agent", token(adapter), "local")
        self._session, self._owner = token(session), token(owner)
        self._active = None
        self._state = "idle"
        self._used = set()
        self._events = []
        self._sequence = 0

    @property
    def state(self):
        return self._state

    @property
    def events(self):
        return tuple(self._events)

    def submit(self, job):
        self._gateway.checkpoint(self._grant, self._req)
        token(job)
        if self._active is not None or job in self._used or len(self._used) >= 64:
            raise Refused("job_unavailable")
        self._used.add(job)
        self._active = Handle(self._session, job, self._req.context, self._owner)
        self._state = "running"
        self._events = []
        self._sequence = 0
        return self._active

    def _owned(self, handle):
        if self._active is None or handle is not self._active:
            raise Refused("not_owned")

    def event(self, handle, sequence, kind):
        self._owned(handle)
        try:
            self._gateway.checkpoint(self._grant, self._req)
        except Refused:
            self._active = None
            self._state = "held"
            self._events.clear()
            raise
        if (type(sequence) is not int or sequence != self._sequence + 1
                or kind not in ("progress", "completed", "failed") or len(self._events) >= 128):
            raise Refused("invalid_event")
        self._sequence = sequence
        self._events.append((sequence, kind))
        if kind != "progress":
            self._state = kind
            self._active = None

    def cancel(self, handle):
        # Cancellation remains available after revocation; no other job touched.
        self._owned(handle)
        self._active = None
        self._state = "cancelled"
        self._events.clear()
