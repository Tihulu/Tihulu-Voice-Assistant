# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded generated-token accounting for the fixed model fixture only.
No provider billing, prompt-token estimate or production authorization.
"""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused
from local_agent_model_fixture_contract import validate_wire,PARENTS

@dataclass(frozen=True,eq=False)
class TokenLease:
    worker: str
    wire_digest: str
    ceiling: int

class TokenLedger:
    def __init__(self,budget):
        if type(budget) is not int or not 16<=budget<=64:
            raise Refused("fixture_token_budget")
        self._budget=budget
        self._owner=current_thread()
        self._active={}
        self._seen=set()
        self._spent=0
        self._closed=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("fixture_token_thread")

    def snapshot(self):
        self._thread()
        reserved=sum(x.ceiling for x in self._active.values())
        return self._budget,self._spent,reserved,self._budget-self._spent-reserved

    def reserve(self,worker,wire):
        self._thread()
        target=validate_wire(worker,wire)
        if self._closed or worker in self._seen or self.snapshot()[3]<16:
            raise Refused("fixture_token_unavailable")
        lease=TokenLease(worker,target,16)
        self._seen.add(worker)
        self._active[worker]=lease
        return lease

    def _owned(self,lease):
        self._thread()
        if type(lease) is not TokenLease or type(lease.worker) is not str or self._active.get(lease.worker) is not lease:
            raise Refused("fixture_token_lease")

    def settle(self,lease,wire,tokens):
        self._owned(lease)
        if self._closed or validate_wire(lease.worker,wire)!=lease.wire_digest:
            raise Refused("fixture_token_wire")
        if type(tokens) is not tuple or not 1<=len(tokens)<=lease.ceiling or any(type(t) is not int or not 0<=t<512 for t in tokens):
            raise Refused("fixture_token_usage")
        self._spent+=len(tokens)
        del self._active[lease.worker]

    def abandon(self,lease):
        """Unknown usage: conservatively charge the entire reserved ceiling."""
        self._owned(lease)
        self._spent+=lease.ceiling
        del self._active[lease.worker]

    def close(self):
        self._thread()
        self._closed=True
        for lease in tuple(self._active.values()):
            self.abandon(lease)
