# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded fixture scheduling, no execution, thread or process creation."""
from dataclasses import dataclass
from gateway import Request, Refused, token

@dataclass(frozen=True)
class Job:
    identity: str
    adapter: str
    memory: int
    priority: int

class Scheduler:
    def __init__(self, gateway, context, slots, memory):
        if type(slots) is not int or not 1 <= slots <= 10 or type(memory) is not int or not 1 <= memory <= 1048576:
            raise Refused("invalid_capacity")
        self._g,self._ctx,self._slots,self._memory = gateway,context,slots,memory
        self._queued = []
        self._running = {}
        self._seen = set()
        self._closed = False

    def queue(self, job, grant):
        if type(job) is not Job:
            raise Refused("invalid_job")
        token(job.identity)
        token(job.adapter)
        if (self._closed or job.identity in self._seen or len(self._seen) >= 64
                or type(job.memory) is not int or not 0 < job.memory <= self._memory
                or type(job.priority) is not int or not 0 <= job.priority <= 100):
            raise Refused("invalid_job")
        req = Request(self._ctx,"agent",job.adapter,"local")
        self._g.checkpoint(grant,req)
        self._seen.add(job.identity)
        self._queued.append((job,grant,req))

    def dispatch(self):
        if self._closed:
            return ()
        available = self._memory - sum(j[0].memory for j in self._running.values())
        selected = []
        remaining = []
        for job,grant,req in sorted(self._queued,key=lambda j:-j[0].priority):
            try:
                self._g.checkpoint(grant,req)
            except Refused:
                continue
            if len(self._running) < self._slots and job.memory <= available:
                handle = object()
                self._running[handle]=(job,grant,req)
                available -= job.memory
                selected.append((handle,job.identity))
            else:
                remaining.append((job,grant,req))
        self._queued = remaining
        return tuple(selected)

    def finish(self, handle):
        if handle not in self._running:
            raise Refused("not_owned")
        job,grant,req = self._running.pop(handle)
        self._g.checkpoint(grant,req)
        return job.identity

    def cancel(self, handle):
        if handle not in self._running:
            raise Refused("not_owned")
        self._running.pop(handle)

    def close(self):
        self._closed=True
        self._queued.clear()
        self._running.clear()
