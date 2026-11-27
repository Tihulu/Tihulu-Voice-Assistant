# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic atomic RAM and VRAM reservations. No device or process access."""
from dataclasses import dataclass
from threading import Lock
from gateway import Refused,token

MAX_MIB=1048576

@dataclass(frozen=True)
class Capacity:
    ram_mib: int
    vram_mib: int

    def checked(self):
        for n in (self.ram_mib,self.vram_mib):
            if type(n) is not int or not 0<=n<=MAX_MIB:
                raise Refused("vector_resource_capacity")
        return self

@dataclass(frozen=True)
class Demand:
    worker: str
    capacity: Capacity

@dataclass(frozen=True,eq=False)
class Reservation:
    run: str
    demand: Demand


class VectorResources:
    """All-or-nothing wave admission, explicit terminal release.

    Capacity measurements are trusted inputs, not device attestations.
    Cancellation does not release a reservation. No automatic CPU fallback.
    """
    def __init__(self,capacity):
        self._capacity=self._check(capacity)
        self._lock=Lock()
        self._active={}
        self._seen=set()
        self._closed=False

    @staticmethod
    def _check(capacity):
        if type(capacity) is not Capacity:
            raise Refused("vector_resource_capacity")
        return capacity.checked()

    def _used(self):
        return Capacity(sum(r.demand.capacity.ram_mib for r in self._active.values()),
                        sum(r.demand.capacity.vram_mib for r in self._active.values()))

    def snapshot(self):
        with self._lock:
            used=self._used()
            return self._capacity,used,Capacity(max(0,self._capacity.ram_mib-used.ram_mib),
                                               max(0,self._capacity.vram_mib-used.vram_mib))

    def reserve_wave(self,run,demands):
        if type(run) is not str or type(demands) is not tuple or not 1<=len(demands)<=10:
            raise Refused("vector_resource_wave")
        token(run)
        for d in demands:
            if type(d) is not Demand or type(d.worker) is not str:
                raise Refused("vector_resource_demand")
            token(d.worker)
            c=self._check(d.capacity)
            if c.ram_mib==0 and c.vram_mib==0:
                raise Refused("vector_resource_demand")
        keys={(run,d.worker) for d in demands}
        if len(keys)!=len(demands):
            raise Refused("vector_resource_duplicate")
        with self._lock:
            if self._closed or keys & self._seen or len(self._seen)+len(keys)>1024 or len(self._active)+len(keys)>64:
                raise Refused("vector_resource_unavailable")
            used=self._used()
            if (used.ram_mib+sum(d.capacity.ram_mib for d in demands)>self._capacity.ram_mib
                    or used.vram_mib+sum(d.capacity.vram_mib for d in demands)>self._capacity.vram_mib):
                raise Refused("vector_resource_exhausted")
            reservations=tuple(Reservation(run,d) for d in demands)
            for r in reservations:
                self._active[(run,r.demand.worker)]=r
            self._seen.update(keys)
            return reservations

    def _owned(self,reservation):
        if (type(reservation) is not Reservation or type(reservation.run) is not str
                or type(reservation.demand) is not Demand or type(reservation.demand.worker) is not str):
            raise Refused("vector_resource_reservation")
        key=(reservation.run,reservation.demand.worker)
        if self._active.get(key) is not reservation:
            raise Refused("vector_resource_reservation")
        return key

    def cancel(self,reservation):
        with self._lock:
            self._owned(reservation)
            # Stop request only; retain both dimensions.

    def release_reaped(self,reservation):
        with self._lock:
            del self._active[self._owned(reservation)]

    def resize(self,capacity):
        capacity=self._check(capacity)
        with self._lock:
            if self._closed:
                raise Refused("vector_resource_closed")
            self._capacity=capacity

    def close(self):
        with self._lock:
            self._closed=True
