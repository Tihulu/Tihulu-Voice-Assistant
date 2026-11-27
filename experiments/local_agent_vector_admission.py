# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping vector plan confirmation and atomic wave ownership.

Trusted coordinator supplies inventory and terminal observations. This does
not grant execution authority, allocate GPU memory or observe real processes.
"""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused,token
from local_agent_vector_planner import vector_plan
from local_agent_vector_resources import VectorResources,Demand

@dataclass(frozen=True,eq=False)
class Offer:
    identity: str
    plan: object

class VectorAdmission:
    def __init__(self,identity,ledger,workers,limit=6,preference="balanced",*,backend="accelerator"):
        if type(identity) is not str or len(identity)>96 or type(ledger) is not VectorResources:
            raise Refused("vector_admission_input")
        token(identity)
        self._identity=identity
        self._ledger=ledger
        self._workers=workers
        self._limit=limit
        self._preference=preference
        self._backend=backend
        self._owner=current_thread()
        self._issued=0
        self._binding=None
        self._pending=None
        self._active=None
        self._index=0
        self._leases={}
        self._stopped=False
        self._closed=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("vector_admission_thread")

    def _open(self):
        self._thread()
        if self._closed:
            raise Refused("vector_admission_closed")

    def bind_session(self,owner):
        """One trusted session for the lifetime of this coordinator."""
        self._open()
        if owner is None or self._binding is not None or self._issued:
            raise Refused("vector_admission_already_bound")
        self._binding=owner

    def _bound(self,owner):
        if owner is not self._binding:
            raise Refused("vector_admission_owner")

    def _plan(self,context,candidates,memory,capacity):
        return vector_plan(context,self._workers,candidates,memory,capacity,self._limit,
                           self._preference,backend=self._backend)

    def prepare(self,context,candidates,memory,*,owner=None):
        self._open()
        self._bound(owner)
        self._pending=None
        if self._leases or (self._active is not None and not self._stopped and self._index<len(self._active.plan.waves)):
            raise Refused("vector_admission_active")
        if self._issued>=64:
            raise Refused("vector_admission_limit")
        plan=self._plan(context,candidates,memory,self._ledger.snapshot()[2])
        self._issued+=1
        self._pending=Offer(self._identity+"-"+str(self._issued),plan)
        return self._pending

    def confirm(self,offer,context,candidates,memory,*,owner=None):
        self._open()
        self._bound(owner)
        if self._pending is None or offer is not self._pending:
            raise Refused("vector_admission_stale")
        self._pending=None
        capacity=self._ledger.snapshot()[2]
        if self._leases or self._plan(context,candidates,memory,capacity)!=offer.plan:
            raise Refused("vector_admission_changed")
        self._confirmed_capacity=capacity
        self._active=offer
        self._index=0
        self._stopped=False

    def reserve_next(self,offer,context,candidates,memory,*,owner=None):
        self._open()
        self._bound(owner)
        if (self._active is None or offer is not self._active or self._stopped or self._leases
                or self._index>=len(offer.plan.waves)):
            raise Refused("vector_admission_wave")
        # Revalidate the original confirmed plan against its original capacity
        # envelope, then perform a fresh atomic reservation on the live ledger.
        try:
            fresh=self._plan(context,candidates,memory,self._confirmed_capacity)
            if fresh!=offer.plan:
                raise Refused("vector_admission_changed")
            demands=tuple(Demand(a.worker,a.capacity) for a in offer.plan.waves[self._index])
            rows=self._ledger.reserve_wave(offer.identity,demands)
        except Refused:
            self.stop(owner=owner)
            raise
        self._leases={r.demand.worker:r for r in rows}
        return rows

    def void_unstarted(self,reservation,*,owner=None):
        """Trusted owner confirms no worker was ever claimed or dispatched.

        Only the combined coordinator can establish that fact. Cancellation
        alone is insufficient. This is not a process exit observation.
        """
        self._thread()
        self._bound(owner)
        if not self._stopped or not any(r is reservation for r in self._leases.values()):
            raise Refused("vector_admission_void")
        self._ledger.release_reaped(reservation)
        del self._leases[reservation.demand.worker]

    def observe_reaped(self,reservation,*,success,owner=None):
        self._thread()
        self._bound(owner)
        if type(success) is not bool or not any(r is reservation for r in self._leases.values()):
            raise Refused("vector_admission_terminal")
        self._ledger.release_reaped(reservation)
        del self._leases[reservation.demand.worker]
        if not success:
            self.stop(owner=owner)
        if not self._leases and not self._stopped:
            self._index+=1

    def completed(self,offer):
        self._thread()
        return (self._active is not None and offer is self._active and not self._stopped and not self._leases
                and self._index==len(offer.plan.waves))

    def stop(self,*,owner=None):
        self._thread()
        self._bound(owner)
        self._pending=None
        self._stopped=True
        for reservation in self._leases.values():
            self._ledger.cancel(reservation)

    def close(self,*,owner=None):
        self.stop(owner=owner)
        self._closed=True
