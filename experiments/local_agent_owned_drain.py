# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted synthetic process ownership and drain protocol. No process or OS calls."""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused,token
from local_agent_resources import ResourceLedger
from local_agent_wave_run import WaveRun

@dataclass(frozen=True,eq=False)
class OwnedWorker:
    worker: str
    attempt: str

class ManagedWave:
    """Separate authorization cancellation from reservation release.

    Exit observation is a trusted fixture event, not proof of actual process
    termination. A real broker must produce that observation only after reaping
    the exact owned process (including descendants where applicable).
    """
    def __init__(self,gateway,plan,workers,candidates,budget,ledger,attempt,
                 limit=6,preference="balanced"):
        if type(ledger) is not ResourceLedger or type(attempt) is not str:
            raise Refused("managed_wave_invalid")
        token(attempt)
        self._owner=current_thread()
        self._ledger=ledger
        self._attempt=attempt
        self._run=WaveRun(gateway,plan,workers,candidates,budget,limit,preference,
                          attempt=attempt)
        self._assignments={a.worker:a for wave in plan.waves for a in wave}
        self._owned={}
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
        return handle

    def stop(self):
        self._thread()
        self._stopping=True
        self._run.cancel()
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

    def observe_exit(self,handle,exit_code,context,candidates,*,payload=None):
        self._thread()
        if type(handle) is not OwnedWorker or type(handle.worker) is not str:
            raise Refused("managed_exit_unknown")
        entry=self._owned.get(handle.worker)
        if entry is None or entry[0] is not handle:
            raise Refused("managed_exit_unknown")
        if type(exit_code) is not int or not -255<=exit_code<=255:
            raise Refused("managed_exit_invalid")
        _,grant,reservation=entry
        try:
            if self._stopping:
                return "DRAINED"
            if exit_code!=0:
                self.stop()
                return "FAILED"
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

    def output(self,worker,context,candidates):
        self._thread()
        if self._stopping or self._owned:
            raise Refused("managed_wave_not_complete")
        return self._run.output(worker,context,candidates)
