# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted synthetic process ownership and drain protocol. No process or OS calls."""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused,token
from local_agent_resources import ResourceLedger
from local_agent_wave_run import WaveRun,MAX_RESULT_BYTES

@dataclass(frozen=True,eq=False)
class OwnedWorker:
    worker: str
    attempt: str

@dataclass(frozen=True,eq=False)
class OwnedMember:
    worker: str
    identity: str

class ManagedWave:
    """Separate authorization cancellation from reservation release.

    Exit observation is a trusted fixture event, not proof of actual process
    termination. A real broker must produce that observation only after reaping
    the exact owned process (including descendants where applicable).
    """
    def __init__(self,gateway,plan,workers,candidates,budget,ledger,attempt,
                 limit=6,preference="balanced",*,strict_results=False):
        if type(ledger) is not ResourceLedger or type(attempt) is not str:
            raise Refused("managed_wave_invalid")
        token(attempt)
        self._owner=current_thread()
        self._ledger=ledger
        self._attempt=attempt
        self._run=WaveRun(gateway,plan,workers,candidates,budget,limit,preference,
                          attempt=attempt,strict_results=strict_results)
        self._assignments={a.worker:a for wave in plan.waves for a in wave}
        self._owned={}
        self._families={}
        self._root_exits={}
        self._stopping=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("managed_wave_thread")

    def request(self,worker):
        self._thread()
        return self._run.request(worker)

    def start(self,worker,grant,context,candidates):
        self._thread()
        if self._stopping or type(worker) is not str or worker not in self._assignments or worker in self._owned:
            raise Refused("managed_worker_unavailable")
        # A stale snapshot must stop an active run even when capacity is
        # exhausted. Do not let the resource refusal mask context invalidation.
        try:
            self._run.validate_plan(context,candidates)
        except Refused:
            self.stop()
            raise
        reservation=self._ledger.reserve(self._attempt,worker,self._assignments[worker].memory_mib)
        try:
            self._run.start(worker,grant,context,candidates)
        except BaseException:
            self._ledger.release(reservation)
            # A stale underlying run is terminal; existing reservations remain
            # owned until each exit is observed.
            if self._run._cancelled:
                self.stop()
            raise
        handle=OwnedWorker(worker,self._attempt)
        self._owned[worker]=(handle,grant,reservation)
        self._families[worker]={}
        return handle

    def stop(self):
        self._thread()
        self._stopping=True
        self._run.cancel()
        # Retain terminal evidence, not stale result bytes.
        for worker,(handle,status,payload) in tuple(self._root_exits.items()):
            self._root_exits[worker]=(handle,status,None)
        return tuple(entry[0] for entry in self._owned.values())

    def pending(self):
        self._thread()
        return tuple(entry[0] for entry in self._owned.values())

    def drained(self):
        self._thread()
        return self._stopping and not self._owned

    def checkpoint(self,handle,context,candidates):
        self._thread()
        if type(handle) is not OwnedWorker or type(handle.worker) is not str:
            raise Refused("managed_exit_unknown")
        entry=self._owned.get(handle.worker)
        if entry is None or entry[0] is not handle:
            raise Refused("managed_exit_unknown")
        if self._stopping:
            raise Refused("managed_wave_draining")
        try:
            self._run.checkpoint_worker(handle.worker,context,candidates,completion_grant=entry[1])
        except Refused:
            self.stop()
            raise

    def register_member(self,handle,identity,context,candidates):
        """Trusted fixture registration before a descendant is started.
        No PID or process discovery; the caller owns the observed family.
        """
        self.checkpoint(handle,context,candidates)
        if type(identity) is not str:
            raise Refused("managed_member_invalid")
        token(identity)
        family=self._families[handle.worker]
        if handle.worker in self._root_exits or identity in family or len(family)>=16:
            raise Refused("managed_member_unavailable")
        member=OwnedMember(handle.worker,identity)
        family[identity]=(member,False)
        return member

    def observe_member_exit(self,member,context,candidates):
        self._thread()
        if type(member) is not OwnedMember or type(member.worker) is not str or type(member.identity) is not str:
            raise Refused("managed_member_unknown")
        family=self._families.get(member.worker,{})
        entry=family.get(member.identity)
        if entry is None or entry[0] is not member or entry[1]:
            raise Refused("managed_member_unknown")
        family[member.identity]=(member,True)
        if not self._stopping:
            try:
                self._run.validate_plan(context,candidates)
                handle,grant,_=self._owned[member.worker]
                self._run.checkpoint_worker(handle.worker,context,candidates,completion_grant=grant)
            except Refused:
                self.stop()
        root=self._root_exits.get(member.worker)
        if root is None or any(not done for _,done in family.values()):
            return "PENDING"
        handle,status,payload=root
        return self._finish_exit(handle,status,context,candidates,payload)

    def observe_exit(self,handle,exit_code,context,candidates,*,payload=None):
        self._thread()
        if type(handle) is not OwnedWorker or type(handle.worker) is not str:
            raise Refused("managed_exit_unknown")
        entry=self._owned.get(handle.worker)
        if entry is None or entry[0] is not handle:
            raise Refused("managed_exit_unknown")
        if type(exit_code) is not int or not -255<=exit_code<=255:
            raise Refused("managed_exit_invalid")
        if handle.worker in self._root_exits:
            raise Refused("managed_exit_duplicate")
        # A root exit is not a family exit. Keep the reservation and stop
        # on root failure even if members still require terminal observation.
        status="DRAINED" if self._stopping else ("FAILED" if exit_code!=0 else "COMPLETED")
        if status=="FAILED":
            self.stop()
        if any(not done for _,done in self._families[handle.worker].values()):
            if not self._stopping:
                try:
                    self._run.checkpoint_worker(handle.worker,context,candidates,completion_grant=entry[1])
                except Refused:
                    self.stop()
                    status="DRAINED"
            if self._stopping:
                payload=None
            elif payload is not None and (type(payload) is not bytes or not 0<len(payload)<=MAX_RESULT_BYTES):
                self.stop()
                status="FAILED"
                payload=None
            self._root_exits[handle.worker]=(handle,status,payload)
            return "PENDING"
        return self._finish_exit(handle,status,context,candidates,payload)

    def _finish_exit(self,handle,status,context,candidates,payload):
        _,grant,reservation=self._owned[handle.worker]
        try:
            if status=="FAILED":
                self.stop()
                return "FAILED"
            if self._stopping or status=="DRAINED":
                return "DRAINED"
            try:
                self._run.complete(handle.worker,context,candidates,payload=payload,
                                   completion_grant=grant)
            except Refused:
                self.stop()
                raise
            return "COMPLETED"
        finally:
            # Only the trusted terminal observation releases this reservation.
            self._ledger.release(reservation)
            del self._owned[handle.worker]
            del self._families[handle.worker]
            self._root_exits.pop(handle.worker,None)

    def inputs(self,handle,context,candidates):
        """Only the exact still-live root receives direct dependency data."""
        self.checkpoint(handle,context,candidates)
        if handle.worker in self._root_exits:
            raise Refused("managed_root_exited")
        return self._run.inputs(handle.worker,context,candidates)

    def output(self,worker,context,candidates):
        self._thread()
        if self._stopping or self._owned:
            raise Refused("managed_wave_not_complete")
        return self._run.output(worker,context,candidates)
