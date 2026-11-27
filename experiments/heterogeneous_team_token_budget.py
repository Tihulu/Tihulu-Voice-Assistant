# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic team-wide generated-token accounting; not a process broker.

A trusted coordinator supplies the confirmed plan digest, budget and attempt
identities. Only trusted transport/exit observations may settle or void leases.
Optional independent prompt budget; no inference, grants, provider billing or OS access.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock

from gateway import Refused, token

MAX_BUDGET = 655360
MAX_ATTEMPTS = 1024
MAX_WAVE = 10
MAX_WIRE = 65536


def _digest(value):
    if (type(value) is not str or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise Refused("team_budget_digest")
    return value


@dataclass(frozen=True)
class Attempt:
    identity: str
    worker: str
    wire_digest: str
    ceiling: int
    prompt_ceiling: int = 0

    def checked(self):
        if type(self.identity) is not str or type(self.worker) is not str:
            raise Refused("team_budget_identity")
        token(self.identity)
        token(self.worker)
        _digest(self.wire_digest)
        if type(self.ceiling) is not int or not 1 <= self.ceiling <= MAX_BUDGET:
            raise Refused("team_budget_ceiling")
        if type(self.prompt_ceiling) is not int or not 0 <= self.prompt_ceiling <= MAX_BUDGET:
            raise Refused("team_budget_prompt_ceiling")
        return self


@dataclass(frozen=True, eq=False)
class Lease:
    plan_digest: str
    attempt: Attempt


class TeamTokenBudget:
    def __init__(self, plan_digest, budget, *, prompt_budget=None):
        self._plan = _digest(plan_digest)
        if type(budget) is not int or not 1 <= budget <= MAX_BUDGET:
            raise Refused("team_budget_limit")
        if prompt_budget is not None and (type(prompt_budget) is not int or not 1 <= prompt_budget <= MAX_BUDGET):
            raise Refused("team_budget_prompt_limit")
        self._budget = budget
        self._prompt_budget = prompt_budget
        self._prompt_spent = 0
        self._spent = 0
        self._active = {}
        self._seen = set()
        self._closed = False
        self._lock = Lock()

    def _reserved(self):
        return sum(lease.attempt.ceiling for lease, _ in self._active.values())

    def _prompt_reserved(self):
        return sum(lease.attempt.prompt_ceiling for lease, _ in self._active.values())

    def prompt_snapshot(self):
        with self._lock:
            if self._prompt_budget is None:
                return None
            reserved = self._prompt_reserved()
            return self._prompt_budget, self._prompt_spent, reserved, self._prompt_budget - self._prompt_spent - reserved

    def snapshot(self):
        with self._lock:
            reserved = self._reserved()
            return self._budget, self._spent, reserved, self._budget - self._spent - reserved

    def reserve_wave(self, plan_digest, attempts):
        """All-or-nothing capacity admission, including simultaneous callers."""
        if _digest(plan_digest) != self._plan:
            raise Refused("team_budget_plan")
        if type(attempts) is not tuple or not 1 <= len(attempts) <= MAX_WAVE:
            raise Refused("team_budget_wave")
        for attempt in attempts:
            if type(attempt) is not Attempt:
                raise Refused("team_budget_attempt")
            attempt.checked()
            if (self._prompt_budget is None) != (attempt.prompt_ceiling == 0):
                raise Refused("team_budget_prompt_mode")
        identities = {a.identity for a in attempts}
        workers = {a.worker for a in attempts}
        if len(identities) != len(attempts) or len(workers) != len(attempts):
            raise Refused("team_budget_duplicate")
        with self._lock:
            if (self._closed or identities & self._seen
                    or len(self._seen) + len(attempts) > MAX_ATTEMPTS):
                raise Refused("team_budget_unavailable")
            if workers & {l.attempt.worker for l, _ in self._active.values()}:
                raise Refused("team_budget_worker_active")
            if sum(a.ceiling for a in attempts) > self._budget - self._spent - self._reserved():
                raise Refused("team_budget_exhausted")
            if self._prompt_budget is not None and sum(a.prompt_ceiling for a in attempts) > self._prompt_budget - self._prompt_spent - self._prompt_reserved():
                raise Refused("team_budget_prompt_exhausted")
            leases = tuple(Lease(self._plan, a) for a in attempts)
            for lease in leases:
                self._active[lease.attempt.identity] = (lease, "PREPARED")
            self._seen.update(identities)
            return leases

    def _owned(self, lease):
        if type(lease) is not Lease or type(lease.attempt) is not Attempt:
            raise Refused("team_budget_lease")
        if type(lease.attempt.identity) is not str:
            raise Refused("team_budget_lease")
        row = self._active.get(lease.attempt.identity)
        if row is None or row[0] is not lease:
            raise Refused("team_budget_lease")
        return row[1]

    def dispatch(self, lease, wire):
        """Record exact bytes before trusted transport; no dispatch after cancel."""
        if type(wire) is not bytes or not 1 <= len(wire) <= MAX_WIRE:
            raise Refused("team_budget_wire")
        digest = sha256(wire).hexdigest()
        with self._lock:
            if self._owned(lease) != "PREPARED" or self._closed:
                raise Refused("team_budget_dispatch")
            if digest != lease.attempt.wire_digest:
                raise Refused("team_budget_wire")
            self._active[lease.attempt.identity] = (lease, "DISPATCHED")

    def cancel(self, lease):
        """Cancellation alone never releases a reservation."""
        with self._lock:
            state = self._owned(lease)
            if not state.startswith("CANCELLED_"):
                self._active[lease.attempt.identity] = (lease, "CANCELLED_" + state)

    def void_not_dispatched(self, lease):
        """Trusted confirmation that no transport was dispatched."""
        with self._lock:
            if self._owned(lease) not in ("PREPARED", "CANCELLED_PREPARED"):
                raise Refused("team_budget_void")
            del self._active[lease.attempt.identity]

    def settle_reaped(self, lease, generated, *, prompt=None):
        """Trusted terminal observation with measured generated-token count."""
        with self._lock:
            if self._owned(lease) not in ("DISPATCHED", "CANCELLED_DISPATCHED"):
                raise Refused("team_budget_settle")
            if type(generated) is not int or not 0 <= generated <= lease.attempt.ceiling:
                raise Refused("team_budget_usage")
            if self._prompt_budget is None:
                if prompt is not None:
                    raise Refused("team_budget_prompt_mode")
            elif type(prompt) is not int or not 0 <= prompt <= lease.attempt.prompt_ceiling:
                raise Refused("team_budget_prompt_usage")
            if self._prompt_budget is not None:
                self._prompt_spent += prompt
            self._spent += generated
            del self._active[lease.attempt.identity]

    def abandon_reaped(self, lease):
        """Trusted terminal observation, unknown usage: charge full ceiling."""
        with self._lock:
            if self._owned(lease) not in ("DISPATCHED", "CANCELLED_DISPATCHED"):
                raise Refused("team_budget_settle")
            self._spent += lease.attempt.ceiling
            self._prompt_spent += lease.attempt.prompt_ceiling
            del self._active[lease.attempt.identity]

    def close(self):
        """No more admissions or dispatches; outstanding leases must drain."""
        with self._lock:
            self._closed = True
