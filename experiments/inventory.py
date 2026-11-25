# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic model inventory and reference ownership, no actual filesystem."""
from dataclasses import dataclass
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
    def __init__(self):
        self._entries={}
        self._refs={}
        self._leases={}
    def register(self, entry):
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
        token(profile)
        entry=self._entries.get(identity)
        if entry is None or entry.kind!=kind or not entry.compatible:
            raise Refused("incompatible_selection")
        key=(profile,kind)
        if key not in self._refs and len(self._refs)>=128:
            raise Refused("reference_limit")
        self._refs[key]=identity
    def lease(self, identity):
        entry=self._entries.get(identity)
        if entry is None or not entry.compatible or len(self._leases)>=64:
            raise Refused("unavailable")
        h=object()
        self._leases[h]=identity
        return h
    def lease_candidate(self, candidate):
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
        return self.lease(candidate.identity)
    def release(self, handle):
        if handle not in self._leases:
            raise Refused("not_owned")
        self._leases.pop(handle)
    def unselect(self, profile, kind):
        self._refs.pop((profile,kind),None)
    def removal_plan(self, identity):
        entry=self._entries.get(identity)
        if entry is None:
            raise Refused("unknown_model")
        aliases={k for k,e in self._entries.items() if e.digest==entry.digest}
        if aliases.intersection(self._refs.values()) or aliases.intersection(self._leases.values()):
            raise Refused("model_in_use")
        return "REFERENCE_ONLY" if not entry.owned or len(aliases)>1 else "REVIEW_OWNED_ARTIFACT"
