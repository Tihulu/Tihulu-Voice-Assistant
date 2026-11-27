# SPDX-License-Identifier: AGPL-3.0-only
"""Bridge a single owned native review to its team's cumulative budget.

This fixture tracks generated tokens only. It does not authorize transport,
prove process exit, or create a provider billing account.
"""
from hashlib import sha256
from threading import current_thread

from gateway import Refused
from heterogeneous_team_token_budget import Attempt, TeamTokenBudget
from local_agent_model_fixture_contract import validate_wire, wire_ceiling


class SharedReviewTokenLedger:
    """Single-use TokenLedger-compatible adapter for the fixed review wire."""

    def __init__(self, budget, plan_digest, attempt_identity):
        if type(budget) is not TeamTokenBudget:
            raise Refused("shared_review_budget")
        self._budget = budget
        self._plan = plan_digest
        self._identity = attempt_identity
        self._owner = current_thread()
        self._lease = None
        self._wire = None
        self._spent = 0
        self._used = False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("shared_review_thread")

    def _owned(self, lease):
        self._thread()
        if self._lease is None or lease is not self._lease:
            raise Refused("shared_review_lease")

    def snapshot(self):
        self._thread()
        reserved = 16 if self._lease is not None else 0
        return 16, self._spent, reserved, 16 - self._spent - reserved

    def reserve(self, worker, wire):
        self._thread()
        if self._used or worker != "a":
            raise Refused("shared_review_used")
        validate_wire(worker, wire)
        if wire_ceiling(worker, wire) != 16:
            raise Refused("shared_review_ceiling")
        spec = Attempt(self._identity, "model-review", sha256(wire).hexdigest(), 16)
        (lease,) = self._budget.reserve_wave(self._plan, (spec,))
        self._lease = lease
        self._wire = wire
        self._used = True
        return lease

    def dispatch(self, lease, wire, actual_wire):
        self._owned(lease)
        if (type(wire) is not bytes or type(actual_wire) is not bytes
                or wire != self._wire or actual_wire != self._wire):
            raise Refused("shared_review_wire")
        self._budget.dispatch(lease, actual_wire)

    def cancel(self):
        self._thread()
        if self._lease is not None:
            self._budget.cancel(self._lease)

    def _clear(self):
        self._lease = self._wire = None

    def void(self, lease):
        self._owned(lease)
        self._budget.void_not_dispatched(lease)
        self._clear()

    def settle(self, lease, wire, tokens):
        self._owned(lease)
        if (type(wire) is not bytes or wire != self._wire
                or type(tokens) is not tuple or not 1 <= len(tokens) <= 16
                or any(type(t) is not int or not 0 <= t < 512 for t in tokens)):
            raise Refused("shared_review_usage")
        self._budget.settle_reaped(lease, len(tokens))
        self._spent = len(tokens)
        self._clear()

    def abandon(self, lease):
        self._owned(lease)
        self._budget.abandon_reaped(lease)
        self._spent = 16
        self._clear()
