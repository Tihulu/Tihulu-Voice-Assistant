# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded generated-token accounting for the fixed model fixture only.
No provider billing, prompt-token estimate or production authorization.
"""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused
from hashlib import sha256
from local_agent_model_fixture_contract import validate_wire,PARENTS
from local_agent_model_stream_contract import stream_wire

@dataclass(frozen=True,eq=False)
class TokenLease:
    worker: str
    wire_digest: str
    ceiling: int
    transport_digest: str = ""
    streaming: bool = False

class TokenLedger:
    def __init__(self,budget,*,strict_dispatch=False):
        if type(budget) is not int or not 16<=budget<=64 or type(strict_dispatch) is not bool:
            raise Refused("fixture_token_budget")
        self._budget=budget
        self._strict_dispatch=strict_dispatch
        self._dispatched=set()
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

    def preflight(self,workers):
        """Non-mutating whole-wave admission on this single owner thread.
        Each actual reservation still checks capacity and exact request.
        """
        self._thread()
        if (self._closed or type(workers) is not tuple or not 1<=len(workers)<=len(PARENTS)
                or any(type(w) is not str or w not in PARENTS or w in self._seen for w in workers)
                or len(set(workers))!=len(workers)):
            raise Refused("fixture_token_wave")
        if len(workers)*16>self.snapshot()[3]:
            raise Refused("fixture_token_wave_budget")

    def reserve(self,worker,wire,*,streaming=False):
        self._thread()
        target=validate_wire(worker,wire)
        if type(streaming) is not bool or (streaming and not self._strict_dispatch):
            raise Refused("fixture_token_transport")
        actual=stream_wire(worker,wire) if streaming else wire
        if self._closed or worker in self._seen or self.snapshot()[3]<16:
            raise Refused("fixture_token_unavailable")
        lease=TokenLease(worker,target,16,sha256(actual).hexdigest(),streaming)
        self._seen.add(worker)
        self._active[worker]=lease
        return lease

    def _owned(self,lease):
        self._thread()
        if type(lease) is not TokenLease or type(lease.worker) is not str or self._active.get(lease.worker) is not lease:
            raise Refused("fixture_token_lease")

    def dispatch(self,lease,wire,transport):
        """Owner records exact wire before giving bytes to the trusted transport."""
        self._owned(lease)
        if not self._strict_dispatch or self._closed or lease in self._dispatched:
            raise Refused("fixture_token_dispatch_state")
        if validate_wire(lease.worker,wire)!=lease.wire_digest:
            raise Refused("fixture_token_wire")
        expected=stream_wire(lease.worker,wire) if lease.streaming else wire
        if type(transport) is not bytes or transport!=expected or sha256(transport).hexdigest()!=lease.transport_digest:
            raise Refused("fixture_token_transport")
        self._dispatched.add(lease)

    def void(self,lease):
        """Trusted fixture no-dispatch observation, not inferred from a timeout."""
        self._owned(lease)
        if not self._strict_dispatch or lease in self._dispatched:
            raise Refused("fixture_token_dispatch_state")
        del self._active[lease.worker]

    def settle(self,lease,wire,tokens):
        self._owned(lease)
        if self._closed or validate_wire(lease.worker,wire)!=lease.wire_digest:
            raise Refused("fixture_token_wire")
        if self._strict_dispatch and (lease not in self._dispatched or lease.streaming):
            raise Refused("fixture_token_wire")
        if type(tokens) is not tuple or not 1<=len(tokens)<=lease.ceiling or any(type(t) is not int or not 0<=t<512 for t in tokens):
            raise Refused("fixture_token_usage")
        self._spent+=len(tokens)
        self._dispatched.discard(lease)
        del self._active[lease.worker]

    def abandon(self,lease):
        """Unknown usage: conservatively charge the entire reserved ceiling."""
        self._owned(lease)
        self._spent+=lease.ceiling
        self._dispatched.discard(lease)
        del self._active[lease.worker]

    def close(self):
        self._thread()
        self._closed=True
        for lease in tuple(self._active.values()):
            if self._strict_dispatch and lease not in self._dispatched:
                self.void(lease)
            else:
                self.abandon(lease)
