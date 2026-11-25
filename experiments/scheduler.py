# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded synthetic scheduler. Serialized ownership, no worker execution."""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from gateway import Request, Refused, token

@dataclass(frozen=True)
class Job:
    identity: str
    adapter: str
    memory: int
    priority: int

    def target(self):
        token(self.identity)
        token(self.adapter)
        if (type(self.memory) is not int or not 0<self.memory<=1048576
                or type(self.priority) is not int or not 0<=self.priority<=100):
            raise Refused("invalid_job")
        return sha256(repr(("schedule",self.identity,self.adapter,
                            self.memory,self.priority)).encode("ascii")).hexdigest()

class Scheduler:
    def __init__(self, gateway, context, slots, memory):
        if type(slots) is not int or not 1 <= slots <= 10 or type(memory) is not int or not 1 <= memory <= 1048576:
            raise Refused("invalid_capacity")
        self._g,self._ctx,self._slots,self._memory = gateway,context,slots,memory
        self._queued = []
        self._running = {}
        self._seen = set()
        self._closed = False
        self._lock = Lock()

    def queue(self, job, grant):
        if type(job) is not Job:
            raise Refused("invalid_job")
        target=job.target()
        with self._lock:
            if (self._closed or job.identity in self._seen or len(self._seen)>=64
                    or job.memory>self._memory):
                raise Refused("invalid_job")
            req=Request(self._ctx,"agent",target,"local")
            self._g.claim(grant,req)
            self._seen.add(job.identity)
            self._queued.append((job,grant,req))

    def _prune(self):
        # Synthetic reservations only; no real process drain is implied.
        kept=[]
        for job,grant,req in self._queued:
            try:
                self._g.checkpoint(grant,req)
            except Refused:
                continue
            kept.append((job,grant,req))
        self._queued=kept
        for handle,(_,grant,req) in tuple(self._running.items()):
            try:
                self._g.checkpoint(grant,req)
            except Refused:
                del self._running[handle]

    def poll(self):
        with self._lock:
            self._prune()
            return (len(self._queued),len(self._running),
                    sum(j[0].memory for j in self._running.values()))

    def cancel_queued(self, identity):
        token(identity)
        with self._lock:
            for i,(job,grant,_) in enumerate(self._queued):
                if job.identity==identity:
                    self._g.revoke(grant.identity)
                    del self._queued[i]
                    return
            raise Refused("not_owned")

    def dispatch(self):
        with self._lock:
            if self._closed:
                return ()
            self._prune()
            available=self._memory-sum(j[0].memory for j in self._running.values())
            selected=[]
            remaining=[]
            for job,grant,req in sorted(self._queued,key=lambda j:-j[0].priority):
                try:
                    self._g.checkpoint(grant,req)
                except Refused:
                    continue
                if len(self._running)<self._slots and job.memory<=available:
                    handle=object()
                    self._running[handle]=(job,grant,req)
                    available-=job.memory
                    selected.append((handle,job.identity))
                else:
                    remaining.append((job,grant,req))
            self._queued=remaining
            return tuple(selected)

    def finish(self, handle):
        with self._lock:
            if type(handle) is not object or handle not in self._running:
                raise Refused("not_owned")
            job,grant,req=self._running.pop(handle)
            self._g.checkpoint(grant,req)
            return job.identity

    def cancel(self, handle):
        with self._lock:
            if type(handle) is not object or handle not in self._running:
                raise Refused("not_owned")
            _,grant,_=self._running.pop(handle)
            self._g.revoke(grant.identity)

    def close(self):
        with self._lock:
            self._closed=True
            for _,grant,_ in (*self._queued,*self._running.values()):
                self._g.revoke(grant.identity)
            self._queued.clear()
            self._running.clear()
