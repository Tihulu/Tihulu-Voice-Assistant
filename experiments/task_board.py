# SPDX-License-Identifier: AGPL-3.0-only
"""Owned synthetic task board; status only, no process, shell or network."""
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
import json
from gateway import Context, FixtureGateway, Request, Refused, token
from scheduler import Scheduler, Job
from connector_protocol import Connector, JobSpec
from event_channel import EventChannel

def _key(*values):
    return sha256(json.dumps(values,separators=(",",":"),ensure_ascii=True).encode("ascii")).hexdigest()

def board_target(room,slots,memory):
    if type(slots) is not int or not 1<=slots<=10 or type(memory) is not int or not 1<=memory<=1048576:
        raise Refused("invalid_capacity")
    return _key("board",token(room),slots,memory)

def board_scope(room,grant_identity):
    return _key("board-scope",token(room),token(grant_identity))

def scoped(room,identity):
    return _key("task",token(room),token(identity))

def plan(room,identity,adapter,capability,payload,memory,priority):
    if type(payload) is not bytes or not 0<len(payload)<=16384:
        raise Refused("invalid_payload")
    ident=scoped(room,identity)
    job=Job(ident,adapter,memory,priority)
    spec=JobSpec(adapter,token(room),ident,capability,sha256(payload).hexdigest())
    job.target()
    spec.target()
    return job,spec

@dataclass(frozen=True)
class Status:
    identity: str
    state: str
    sequence: int

@dataclass(frozen=True)
class Receipt:
    identity: str
    scope: str
    spec_target: str
    payload_digest: str

@dataclass
class _Record:
    job: Job
    spec: JobSpec
    payload: bytes
    schedule_grant: object
    connector_grant: object
    state: str="queued"
    sequence: int=0
    handle: object=None
    connector: object=None
    channel: object=None

class TaskBoard:
    def __init__(self,gateway,context,room,slots,memory,grant):
        if type(gateway) is not FixtureGateway or type(context) is not Context or gateway.context!=context or context.mode=="CLOUD_CHAT":
            raise Refused("local_context_required")
        req=Request(context,"agent",board_target(room,slots,memory),"local")
        gateway.claim(grant,req)
        self._root=(grant,req)
        self._g,self._ctx,self._room=gateway,context,board_scope(room,grant.identity)
        self._scheduler=Scheduler(gateway,context,slots,memory)
        self._records={}
        self._closed=False
        self._lock=RLock()
        try:
            self._open()
        except BaseException:
            self.close()
            raise

    def _open(self):
        if self._closed:
            raise Refused("board_closed")
        try:
            self._g.checkpoint(*self._root)
        except Refused:
            self.close()
            raise

    def _requests(self,r):
        return ((r.schedule_grant,Request(self._ctx,"agent",r.job.target(),"local")),
                (r.connector_grant,Request(self._ctx,"agent",r.spec.target(),"local")))

    def _check(self,r):
        for g,req in self._requests(r):
            self._g.checkpoint(g,req)

    def _stop(self,r,state):
        try:
            if r.channel is not None:
                r.channel.close()
        finally:
            try:
                if r.connector is not None:
                    r.connector.close()
            finally:
                try:
                    if r.state=="queued":
                        self._scheduler.cancel_queued(r.job.identity)
                    elif r.handle is not None:
                        self._scheduler.cancel(r.handle)
                except Refused:
                    pass
                finally:
                    for g,_ in self._requests(r):
                        self._g.revoke(g.identity)
                    r.payload=b""
                    r.handle=r.connector=r.channel=None
                    r.state=state

    def queue(self,identity,adapter,capability,payload,memory,priority,schedule_grant,connector_grant):
        with self._lock:
            self._open()
            token(identity)
            if identity in self._records or len(self._records)>=16:
                raise Refused("task_limit_or_duplicate")
            job,spec=plan(self._room,identity,adapter,capability,payload,memory,priority)
            r=_Record(job,spec,payload,schedule_grant,connector_grant)
            self._check(r)
            self._scheduler.queue(job,schedule_grant)
            self._records[identity]=r
            try:
                self._check(r)
            except BaseException:
                self._stop(r,"held")
                raise
            return Status(identity,"queued",0)

    def _poll(self):
        self._open()
        for r in self._records.values():
            if r.state in ("queued","running","completed","failed"):
                try:
                    self._check(r)
                except Refused:
                    self._stop(r,"held")
        self._scheduler.poll()

    def dispatch(self):
        with self._lock:
            self._poll()
            result=[]
            by_job={r.job.identity:(i,r) for i,r in self._records.items()}
            selected=self._scheduler.dispatch()
            for handle,j in selected:
                _,r=by_job[j]
                r.handle=handle
                r.state="running"
            for handle,j in selected:
                identity,r=by_job[j]
                try:
                    self._check(r)
                    r.connector=Connector(self._g,self._ctx,r.spec.adapter,r.spec.session)
                    owned=r.connector.begin(r.spec,r.connector_grant,r.payload)
                    r.payload=b""
                    r.channel=EventChannel(r.connector,owned,r.spec.session,r.spec.job)
                    self._check(r)
                    self._open()
                    result.append(Status(identity,"running",0))
                except BaseException:
                    self._stop(r,"held")
                    # No undisclosed running reservation survives a batch failure.
                    for _,other_job in selected:
                        _,other=by_job[other_job]
                        if other.state=="running":
                            self._stop(other,"held")
                    raise
            return tuple(result)

    def receive(self,identity,raw):
        with self._lock:
            self._poll()
            r=self._records.get(token(identity))
            if r is None or r.state!="running":
                raise Refused("task_not_running")
            try:
                state=r.channel.receive(raw)
                self._check(r)
                r.sequence+=1
                if state!="running":
                    self._scheduler.finish(r.handle)
                    r.handle=None
                    r.channel.close()
                    r.connector.close()
                    r.channel=r.connector=None
                    self._check(r)
                    r.state=state
                    if state=="cancelled":
                        self._stop(r,state)
                self._open()
                return Status(identity,r.state,r.sequence)
            except BaseException:
                self._stop(r,"held")
                raise

    def cancel(self,identity):
        with self._lock:
            self._open()
            r=self._records.get(token(identity))
            if r is None or r.state not in ("queued","running"):
                raise Refused("task_not_active")
            self._stop(r,"cancelled")
            return Status(identity,"cancelled",r.sequence)

    def snapshot(self):
        with self._lock:
            self._poll()
            return tuple(Status(i,r.state,r.sequence) for i,r in self._records.items())

    def receipt(self,identity):
        with self._lock:
            self._poll()
            r=self._records.get(token(identity))
            if r is None or r.state!="completed" or r.spec.capability!="local-analysis":
                raise Refused("completed_analysis_required")
            self._check(r)
            return Receipt(identity,self._room,r.spec.target(),r.spec.payload_digest)

    def capacity(self):
        with self._lock:
            self._poll()
            return self._scheduler.poll()

    def close(self):
        with self._lock:
            self._closed=True
            try:
                for r in self._records.values():
                    self._stop(r,"closed")
            finally:
                self._scheduler.close()
                self._records.clear()
                self._g.revoke(self._root[0].identity)
