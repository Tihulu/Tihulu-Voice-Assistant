# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic model inventory and reference ownership, no actual filesystem."""
from dataclasses import dataclass
from threading import RLock
from gateway import Refused, token

@dataclass(frozen=True)
class Entry:
    identity: str
    digest: str
    kind: str
    runtime: str
    compatible: bool
    owned: bool

class Inventory:
    def __init__(self, capacity_mib=None):
        if capacity_mib is not None and (type(capacity_mib) is not int or not 1<=capacity_mib<=1048576):
            raise Refused("invalid_capacity")
        self._capacity=capacity_mib
        self._charges={}
        self._entries={}
        self._refs={}
        self._leases={}
        self._lock=RLock()
        self._groups={}
    def register(self, entry):
        with self._lock:
            return self._register(entry)

    def _register(self, entry):
        if type(entry) is not Entry:
            raise Refused("invalid_entry")
        for v in (entry.identity,entry.runtime):
            token(v)
        if (not isinstance(entry.digest,str) or len(entry.digest)!=64
                or any(c not in "0123456789abcdef" for c in entry.digest)
                or entry.kind not in ("stt","llm","tts")
                or type(entry.compatible) is not bool or type(entry.owned) is not bool
                or entry.identity in self._entries or len(self._entries)>=64):
            raise Refused("invalid_entry")
        if any(e.digest==entry.digest and (e.kind!=entry.kind or e.owned!=entry.owned)
               for e in self._entries.values()):
            raise Refused("identity_conflict")
        self._entries[entry.identity]=entry
    def select(self, profile, kind, identity):
        with self._lock:
            return self._select(profile, kind, identity)

    def _select(self, profile, kind, identity):
        token(profile)
        entry=self._entries.get(identity)
        if entry is None or entry.kind!=kind or not entry.compatible:
            raise Refused("incompatible_selection")
        key=(profile,kind)
        if key not in self._refs and len(self._refs)>=128:
            raise Refused("reference_limit")
        self._refs[key]=identity
    def lease(self, identity):
        with self._lock:
            if self._capacity is not None:
                raise Refused("candidate_required_for_metered_inventory")
            return self._lease(identity)

    def _check_capacity(self, memory):
        if self._capacity is not None and sum(self._charges.values())+memory>self._capacity:
            raise Refused("aggregate_capacity_exceeded")

    def capacity_snapshot(self):
        with self._lock:
            return self._capacity,sum(self._charges.values()),len(self._leases)

    def _lease(self, identity):
        token(identity)
        entry=self._entries.get(identity)
        if entry is None or not entry.compatible or len(self._leases)>=64:
            raise Refused("unavailable")
        h=object()
        self._leases[h]=identity
        return h
    def lease_candidate(self, candidate):
        with self._lock:
            return self._lease_candidate(candidate)

    def _lease_candidate(self, candidate, *, group=False):
        # Match the trusted selection metadata to the inventory record before
        # issuing a fixture lease. This does not open or verify real weights.
        from inference import Candidate
        if type(candidate) is not Candidate:
            raise Refused("invalid_candidate")
        candidate.checked()
        entry=self._entries.get(candidate.identity)
        if (candidate.locality!="local" or not candidate.compatible or not candidate.healthy
                or entry is None or entry.kind!=candidate.task or entry.runtime!=candidate.provider):
            raise Refused("candidate_inventory_mismatch")
        if not group:
            self._check_capacity(candidate.memory_mib)
        handle=self._lease(candidate.identity)
        if not group:
            self._charges[handle]=candidate.memory_mib
        return handle
    def release(self, handle):
        with self._lock:
            if any(handle is h for group in self._groups.values() for h in group):
                raise Refused("group_owned_lease")
            return self._release(handle)

    def _release(self, handle):
        if type(handle) is not object or handle not in self._leases:
            raise Refused("not_owned")
        self._leases.pop(handle)
        self._charges.pop(handle,None)
    def unselect(self, profile, kind):
        with self._lock:
            return self._unselect(profile, kind)

    def _unselect(self, profile, kind):
        self._refs.pop((profile,kind),None)
    def removal_plan(self, identity):
        with self._lock:
            return self._removal_plan(identity)

    def _removal_plan(self, identity):
        entry=self._entries.get(identity)
        if entry is None:
            raise Refused("unknown_model")
        aliases={k for k,e in self._entries.items() if e.digest==entry.digest}
        if aliases.intersection(self._refs.values()) or aliases.intersection(self._leases.values()):
            raise Refused("model_in_use")
        return "REFERENCE_ONLY" if not entry.owned or len(aliases)>1 else "REVIEW_OWNED_ARTIFACT"

    def lease_pipeline(self, expected, context, candidates):
        """All-or-nothing synthetic reservations; never loads model weights."""
        from voice_pipeline import revalidate
        with self._lock:
            plan=revalidate(expected,context,candidates)
            # Sequential plans assume verified unloading; without runtime proof
            # charge all resident models plus reserve, never the smaller peak.
            charge=sum(c.memory_mib for c in plan.models)+plan.reserve_mib
            self._check_capacity(charge)
            handles=[]
            try:
                for candidate in plan.models:
                    handles.append(self._lease_candidate(candidate,group=True))
            except BaseException:
                for handle in handles:
                    self._release(handle)
                raise
            group=object()
            self._groups[group]=tuple(handles)
            self._charges[group]=charge
            return group

    def release_pipeline(self, group):
        with self._lock:
            if type(group) is not object or group not in self._groups:
                raise Refused("not_owned")
            handles=self._groups[group]
            for handle in handles:
                self._release(handle)
            del self._groups[group]
            self._charges.pop(group,None)
