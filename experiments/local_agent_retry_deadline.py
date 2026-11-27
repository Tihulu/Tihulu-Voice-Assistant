# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-thread monotonic deadlines for disposable retry fixtures.

No process control or timer threads. A deadline is a stop request, not a
terminal process observation. The trusted owner must poll and drain.
"""
from dataclasses import dataclass
from threading import current_thread
from time import monotonic_ns
from gateway import Refused

MAX_DURATION_NS=3_600_000_000_000

@dataclass(frozen=True,eq=False)
class Deadline:
    identity: str
    expires_ns: int


class RetryDeadlines:
    def __init__(self,total_ns,worker_ns,*,clock=monotonic_ns):
        for value in (total_ns,worker_ns):
            if type(value) is not int or not 1<=value<=MAX_DURATION_NS:
                raise Refused("retry_deadline_limit")
        if worker_ns>total_ns or not callable(clock):
            raise Refused("retry_deadline_limit")
        self._owner=current_thread()
        self._clock=clock
        self._last=None
        self._failed=False
        self._worker_ns=worker_ns
        self._session_end=self._now()+total_ns
        self._active={}
        self._seen=set()

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("retry_deadline_thread")

    def _now(self):
        self._thread()
        if self._failed:
            raise Refused("retry_deadline_clock")
        try:
            now=self._clock()
            if type(now) is not int or now<0 or (self._last is not None and now<self._last):
                raise ValueError("clock")
        except Exception as exc:
            self._failed=True
            raise Refused("retry_deadline_clock") from exc
        self._last=now
        return now

    def check(self):
        now=self._now()
        if now>=self._session_end:
            raise Refused("retry_deadline_session_expired")
        if any(now>=ticket.expires_ns for ticket in self._active.values()):
            raise Refused("retry_deadline_worker_expired")
        return now

    def start(self,identity):
        now=self.check()
        if type(identity) is not str or len(identity)!=64 or any(c not in "0123456789abcdef" for c in identity):
            raise Refused("retry_deadline_identity")
        if identity in self._seen or len(self._seen)>=1024:
            raise Refused("retry_deadline_duplicate")
        ticket=Deadline(identity,min(self._session_end,now+self._worker_ns))
        self._active[identity]=ticket
        self._seen.add(identity)
        return ticket

    def remaining_ns(self,ticket):
        self._thread()
        if type(ticket) is not Deadline or type(ticket.identity) is not str or self._active.get(ticket.identity) is not ticket:
            raise Refused("retry_deadline_ticket")
        return ticket.expires_ns-self.check()

    def finish(self,ticket):
        """Trusted exact terminal observation; valid even after timeout."""
        self._thread()
        if type(ticket) is not Deadline or type(ticket.identity) is not str or self._active.get(ticket.identity) is not ticket:
            raise Refused("retry_deadline_ticket")
        del self._active[ticket.identity]
