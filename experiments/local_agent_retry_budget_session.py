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
from local_agent_model_fixture_contract import PARENTS, DEFAULT_CEILINGS, ECONOMY_CEILINGS, MAX_PROMPT_TOKENS, wire_for, bound_reply
from heterogeneous_team_token_budget import TeamTokenBudget, Attempt


@dataclass(frozen=True, eq=False)
class RetryOffer:
    attempt: str
    plan_digest: str
    profile: str
    ceilings: tuple
    remaining: int
    prompt_remaining: int | None = None
    prompt_policy: str = "whole-plan"
    prompt_required_at_admission: int = 0


class RetryBudgetSession:
    def __init__(self, coordinator, total, *, prompt_total=None):
        if (type(coordinator) is not AttemptCoordinator or not coordinator._owned_drain
                or not coordinator._strict_results or coordinator._issued
                or type(total) is not int or not 16 <= total <= 655360):
            raise Refused("retry_budget_session")
        if prompt_total is not None and (type(prompt_total) is not int or not 1 <= prompt_total <= 655360):
            raise Refused("retry_budget_prompt_limit")
        coordinator._thread()
        scope = sha256(("retry-budget-v1:"+coordinator._identity).encode("ascii")).hexdigest()
        coordinator._gateway.register_agent_attempt(scope)
        self._coordinator = coordinator
        self._scope = scope
        self._budget = TeamTokenBudget(scope,total,prompt_budget=prompt_total)
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

    def prompt_snapshot(self):
        self._thread()
        return self._budget.prompt_snapshot()

    def _open(self):
        self._thread()
        if self._closed:
            raise Refused("retry_budget_closed")

    def prepare(self, context, candidates, profile, *, prompt_policy="whole-plan"):
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
        prompt = self.prompt_snapshot()
        if type(prompt_policy) is not str or prompt_policy not in ("whole-plan","wave") or (prompt is None and prompt_policy != "whole-plan"):
            raise Refused("retry_budget_prompt_policy")
        # Wave admission is an explicit alternative: it does not promise that
        # all later waves fit. Each wave still reserves the full context cap.
        required = 0 if prompt is None else MAX_PROMPT_TOKENS*(len(PARENTS) if prompt_policy == "whole-plan" else 1)
        if prompt is not None and (prompt[2] or prompt[3] < required):
            raise Refused("retry_budget_prompt_unaffordable")
        preview = self._coordinator.prepare(context,candidates)
        if tuple(tuple(a.worker for a in w) for w in preview.plan.waves) != (("plan",),("a","b"),("review",)):
            raise Refused("retry_budget_plan_shape")
        offer = RetryOffer(preview.attempt,preview.plan.digest,profile,ceilings,remaining,
                           None if prompt is None else prompt[3],prompt_policy,required)
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
        prompt = self.prompt_snapshot()
        if (None if prompt is None else prompt[3]) != offer.prompt_remaining or (prompt is not None and prompt[2]):
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
        try:
            run._run.validate_plan(context,candidates)
        except Refused:
            self.stop()
            raise
        if run.pending():
            raise Refused("retry_budget_pending")
        wave = tuple(a.worker for a in run._run._wave())
        if type(names) is not tuple or not wave or names != wave:
            raise Refused("retry_budget_wave")
        if run._run._claims:
            try:
                run._run._gateway.checkpoint_claimed_group(tuple(run._run._claims.values()))
            except Refused:
                self.stop()
                raise
        limits = dict(zip(PARENTS,offer.ceilings))
        wires = tuple(wire_for(n,tuple(run._run._outputs[p] for p in PARENTS[n]),ceiling=limits[n]) for n in wave)
        attempts = tuple(Attempt(sha256((offer.attempt+":"+offer.plan_digest+":"+n).encode("ascii")).hexdigest(),
                                  n,sha256(w).hexdigest(),limits[n],MAX_PROMPT_TOKENS if offer.prompt_remaining is not None else 0) for n,w in zip(wave,wires))
        try:
            leases = self._budget.reserve_wave(self._scope,attempts)
        except Refused:
            # A capacity refusal at a later wave ends this attempt. Retain
            # actual prior charges and revoke unpublished intermediate output.
            self.stop()
            raise
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
            _,tokens,prompt = bound_reply(l.attempt.worker,wire,envelope)
        except (ValueError,RecursionError):
            self._budget.abandon_reaped(l)
            del self._leases[l.attempt.worker]
            self.stop()
            raise Refused("retry_budget_unknown_usage")
        self._budget.settle_reaped(l,len(tokens),prompt=prompt if l.attempt.prompt_ceiling else None)
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
