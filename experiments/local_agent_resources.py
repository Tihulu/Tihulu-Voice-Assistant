# SPDX-License-Identifier: AGPL-3.0-only
"""Shared synthetic memory reservations across independent local agent runs.
No GPU allocation, device query, process control or real lease authorization.
"""
from dataclasses import dataclass
from threading import Lock
from gateway import Refused,token

@dataclass(frozen=True,eq=False)
class Reservation:
    run: str
    worker: str
    memory_mib: int

class ResourceLedger:
    def __init__(self,capacity_mib):
        self._valid_capacity(capacity_mib)
        self._capacity=capacity_mib
        self._lock=Lock()
        self._active={}
        self._closed=False

    @staticmethod
    def _valid_capacity(value):
        if type(value) is not int or not 0<=value<=1048576:
            raise Refused("invalid_resource_capacity")

    def snapshot(self):
        with self._lock:
            used=sum(r.memory_mib for r in self._active.values())
            return self._capacity,used,max(0,self._capacity-used)

    def resize(self,capacity_mib):
        """A trusted measurement may shrink capacity; never evict active work."""
        self._valid_capacity(capacity_mib)
        with self._lock:
            if self._closed:
                raise Refused("resource_ledger_closed")
            self._capacity=capacity_mib

    def reserve(self,run,worker,memory_mib):
        if type(run) is not str or type(worker) is not str:
            raise Refused("invalid_reservation_identity")
        token(run)
        token(worker)
        if type(memory_mib) is not int or not 0<memory_mib<=1048576:
            raise Refused("invalid_reservation_size")
        key=(run,worker)
        with self._lock:
            if self._closed or key in self._active:
                raise Refused("reservation_unavailable")
            if len(self._active)>=64:
                raise Refused("reservation_limit")
            if sum(r.memory_mib for r in self._active.values())+memory_mib>self._capacity:
                raise Refused("resource_capacity_exceeded")
            reservation=Reservation(run,worker,memory_mib)
            self._active[key]=reservation
            return reservation

    def release(self,reservation):
        if type(reservation) is not Reservation:
            raise Refused("invalid_reservation")
        if type(reservation.run) is not str or type(reservation.worker) is not str:
            raise Refused("invalid_reservation")
        with self._lock:
            key=(reservation.run,reservation.worker)
            if self._active.get(key) is not reservation:
                raise Refused("reservation_not_owned")
            del self._active[key]

    def close(self):
        """No new reservations; active owners must explicitly release."""
        with self._lock:
            self._closed=True
