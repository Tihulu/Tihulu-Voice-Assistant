# SPDX-License-Identifier: AGPL-3.0-only
"""Owned fixed model-review reservation fixture, not a process broker.
Only a trusted caller can report definite absence or family termination.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import current_thread
from gateway import Refused
from heterogeneous_model_review import MixedModelReview
from local_agent_model_token_ledger import TokenLedger
from local_agent_model_fixture_contract import bound_reply

@dataclass(frozen=True,eq=False)
class ReviewTicket:
    wire_digest: str

class OwnedMixedReview:
    def __init__(self,review,*,generated_budget=16):
        if type(review) is not MixedModelReview or type(generated_budget) is not int or generated_budget!=16:
            raise Refused("mixed_owned_budget")
        self._review=review
        self._owner=current_thread()
        self._ledger=review._team._ledger
        self._tokens=TokenLedger(generated_budget,strict_dispatch=True)
        review._g.register_agent_attempt("mixed-owned:"+review.request().target)
        self._state="READY"
        self._ticket=None
        self._reservation=None
        self._lease=None
        self._wire=None
        self._stopping=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("mixed_owned_thread")

    def _owned(self,ticket,state):
        self._thread()
        if type(ticket) is not ReviewTicket or ticket is not self._ticket or self._state!=state:
            raise Refused("mixed_owned_ticket")

    def pending(self):
        self._thread()
        return self._ticket

    def usage(self):
        self._thread()
        return self._tokens.snapshot()

    def prepare(self,grant,context,available,models):
        self._thread()
        if self._state!="READY" or self._stopping:
            raise Refused("mixed_owned_state")
        r=self._ledger.reserve(self._review.request().target,"model-review",16)
        try:
            wire=self._review.begin(grant,context,available,models)
            lease=self._tokens.reserve("a",wire)
        except BaseException:
            self._review.stop()
            self._ledger.release(r)
            self._state="CLOSED"
            raise
        self._reservation=r
        self._lease=lease
        self._wire=wire
        self._ticket=ReviewTicket(sha256(wire).hexdigest())
        self._state="PREPARED"
        return self._ticket,wire

    def dispatch(self,ticket,wire,context,available,models):
        self._owned(ticket,"PREPARED")
        if self._stopping:
            raise Refused("mixed_owned_stopped")
        try:
            self._review._check(context,available,models)
            if type(wire) is not bytes or wire!=self._wire:
                raise Refused("mixed_owned_wire")
            self._tokens.dispatch(self._lease,wire,wire)
            self._state="DISPATCHED"
        except Refused:
            self.stop()
            raise

    def stop(self):
        self._thread()
        self._stopping=True
        self._review.stop()
        # Neither cancellation nor timeout is a terminal observation.

    def not_started(self,ticket):
        self._owned(ticket,"PREPARED")
        self.stop()
        self._tokens.void(self._lease)
        self._release()

    def _release(self):
        self._ledger.release(self._reservation)
        self._ticket=self._reservation=self._lease=self._wire=None
        self._state="CLOSED"

    def observe_reaped(self,ticket,exit_code,envelope,context,available,models):
        """Trusted exact family exit; malformed output conservatively charged.
        A successful wrapper envelope is not independent OS attestation.
        """
        self._owned(ticket,"DISPATCHED")
        if type(exit_code) is not int or not -255<=exit_code<=255:
            raise Refused("mixed_owned_exit")
        try:
            if exit_code!=0:
                was_stopping=self._stopping
                self._tokens.abandon(self._lease)
                self.stop()
                return "DRAINED" if was_stopping else "FAILED"
            try:
                _,tokens,_=bound_reply("a",self._wire,envelope)
                self._tokens.settle(self._lease,self._wire,tokens)
            except (ValueError,RecursionError) as exc:
                self._tokens.abandon(self._lease)
                self.stop()
                raise Refused("mixed_owned_reply") from exc
            if self._stopping:
                return "DRAINED"
            self._review.complete(envelope,context,available,models)
            return "COMPLETED"
        except Refused:
            self.stop()
            raise
        finally:
            self._release()

    def output(self,context,available,models):
        self._thread()
        if self._state!="CLOSED" or self._stopping:
            raise Refused("mixed_owned_unpublished")
        return self._review.output(context,available,models)
