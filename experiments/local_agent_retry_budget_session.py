# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping retry budget admission for the fixed local four-worker fixture.

A trusted coordinator owns one cumulative generated-token ledger across fresh
confirmed plans. No implicit profile downgrade, cloud routing, process launch,
or real billing. Terminal observations remain the caller's responsibility.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import current_thread

from gateway import Refused
from local_agent_replan import AttemptCoordinator
from local_agent_model_fixture_contract import PARENTS, DEFAULT_CEILINGS, ECONOMY_CEILINGS, wire_for, bound_reply
from heterogeneous_team_token_budget import TeamTokenBudget, Attempt


@dataclass(frozen=True, eq=False)
class RetryOffer:
    attempt: str
    plan_digest: str
    profile: str
    ceilings: tuple
    remaining: int


class RetryBudgetSession:
    def __init__(self, coordinator, total):
        if (type(coordinator) is not AttemptCoordinator or not coordinator._owned_drain
                or not coordinator._strict_results or coordinator._issued
                or type(total) is not int or not 16 <= total <= 655360):
            raise Refused("retry_budget_session")
        coordinator._thread()
        scope = sha256(("retry-budget-v1:"+coordinator._identity).encode("ascii")).hexdigest()
        coordinator._gateway.register_agent_attempt(scope)
        self._coordinator = coordinator
        self._scope = scope
        self._budget = TeamTokenBudget(scope,total)
        self._owner = current_thread()
        self._pending = None
        self._active = None
        self._leases = {}
        self._stopped = False
        self._closed = False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("retry_budget_thread")

    def snapshot(self):
        self._thread()
        return self._budget.snapshot()

    def _open(self):
        self._thread()
        if self._closed:
            raise Refused("retry_budget_closed")

    def prepare(self, context, candidates, profile):
        self._open()
        self._pending = None
        if self._active is not None:
            _, run = self._active
            if not self._stopped or self._leases or not run.drained():
                raise Refused("retry_budget_active")
            self._active = None
        if type(profile) is not str or profile not in ("standard","economy"):
            raise Refused("retry_budget_profile")
        ceilings = DEFAULT_CEILINGS if profile == "standard" else ECONOMY_CEILINGS
        _, _, reserved, remaining = self.snapshot()
        if reserved or sum(ceilings) > remaining:
            raise Refused("retry_budget_unaffordable")
        preview = self._coordinator.prepare(context,candidates)
        if tuple(tuple(a.worker for a in w) for w in preview.plan.waves) != (("plan",),("a","b"),("review",)):
            raise Refused("retry_budget_plan_shape")
        offer = RetryOffer(preview.attempt,preview.plan.digest,profile,ceilings,remaining)
        self._pending = (offer,preview)
        return offer

    def confirm(self, offer, context, candidates):
        self._open()
        if self._pending is None or offer is not self._pending[0]:
            raise Refused("retry_budget_stale")
        _, preview = self._pending
        self._pending = None
        if self._active is not None or self.snapshot()[2:] != (0,offer.remaining):
            raise Refused("retry_budget_changed")
        run = self._coordinator.confirm(preview,context,candidates)
        self._active = (offer,run)
        self._stopped = False
        return run

    def reserve_wave(self, offer, names, context, candidates):
        self._open()
        if self._active is None or offer is not self._active[0] or self._stopped or self._leases:
            raise Refused("retry_budget_wave")
        run = self._active[1]
        run._run.validate_plan(context,candidates)
        if run.pending():
            raise Refused("retry_budget_pending")
        wave = tuple(a.worker for a in run._run._wave())
        if type(names) is not tuple or not wave or names != wave:
            raise Refused("retry_budget_wave")
        if run._run._claims:
            run._run._gateway.checkpoint_claimed_group(tuple(run._run._claims.values()))
        limits = dict(zip(PARENTS,offer.ceilings))
        wires = tuple(wire_for(n,tuple(run._run._outputs[p] for p in PARENTS[n]),ceiling=limits[n]) for n in wave)
        attempts = tuple(Attempt(sha256((offer.attempt+":"+offer.plan_digest+":"+n).encode("ascii")).hexdigest(),
                                  n,sha256(w).hexdigest(),limits[n]) for n,w in zip(wave,wires))
        leases = self._budget.reserve_wave(self._scope,attempts)
        self._leases = {l.attempt.worker:(l,w,"PREPARED") for l,w in zip(leases,wires)}
        return tuple(zip(leases,wires))

    def _entry(self, lease):
        self._thread()
        if not any(lease is l for l,_,_ in self._leases.values()):
            raise Refused("retry_budget_lease")
        return self._leases[lease.attempt.worker]

    def dispatch(self, lease, wire, context, candidates):
        self._open()
        l,expected,state = self._entry(lease)
        if self._stopped or state != "PREPARED" or type(wire) is not bytes or wire != expected:
            raise Refused("retry_budget_dispatch")
        run = self._active[1]
        handles = tuple(h for h in run.pending() if h.worker == l.attempt.worker)
        if len(handles) != 1:
            raise Refused("retry_budget_unclaimed")
        rows = run.inputs(handles[0],context,candidates)
        if wire_for(l.attempt.worker,rows,ceiling=l.attempt.ceiling) != expected:
            raise Refused("retry_budget_wire_drift")
        self._budget.dispatch(l,wire)
        self._leases[l.attempt.worker] = (l,expected,"DISPATCHED")

    def settle_reaped(self, lease, envelope):
        """Trusted terminal observation, with exact measured or unknown usage."""
        l,wire,state = self._entry(lease)
        if state != "DISPATCHED":
            raise Refused("retry_budget_terminal")
        try:
            _,tokens,_ = bound_reply(l.attempt.worker,wire,envelope)
        except (ValueError,RecursionError):
            self._budget.abandon_reaped(l)
            del self._leases[l.attempt.worker]
            self.stop()
            raise Refused("retry_budget_unknown_usage")
        self._budget.settle_reaped(l,len(tokens))
        del self._leases[l.attempt.worker]
        return len(tokens)

    def abandon_reaped(self, lease):
        l,_,state = self._entry(lease)
        if state != "DISPATCHED":
            raise Refused("retry_budget_terminal")
        self._budget.abandon_reaped(l)
        del self._leases[l.attempt.worker]
        self.stop()

    def void_not_dispatched(self, lease):
        l,_,state = self._entry(lease)
        if not self._stopped or state != "PREPARED":
            raise Refused("retry_budget_terminal")
        self._budget.void_not_dispatched(l)
        del self._leases[l.attempt.worker]

    def stop(self):
        self._thread()
        self._pending = None
        self._stopped = True
        for lease,_,_ in self._leases.values():
            self._budget.cancel(lease)
        # Keep the exact active run even when coordinator clears its pointer.
        self._coordinator.stop()

    def close(self):
        self._thread()
        self.stop()
        self._closed = True
        self._budget.close()
        self._coordinator.close()
