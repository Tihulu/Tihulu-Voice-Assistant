# SPDX-License-Identifier: AGPL-3.0-only
"""One-shot fixed-fixture whole-plan token admission; no runtime activation."""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused
from local_agent_replan import AttemptCoordinator,ReplanPreview
from local_agent_model_fixture_contract import PARENTS,DEFAULT_CEILINGS,ECONOMY_CEILINGS
from local_agent_model_token_ledger import TokenLedger

@dataclass(frozen=True,eq=False)
class BudgetOffer:
    attempt: str
    plan_digest: str
    profile: str
    budget: int
    ceilings: tuple
    required: int

class BudgetAdmission:
    """Explicit profile selection, no silent downgrade or budget increase.
    One confirmed attempt only; cumulative budget across retries is separate.
    Identity binds a trusted fixture preview, not a real UI consent event.
    """
    def __init__(self,coordinator,budget):
        if type(coordinator) is not AttemptCoordinator or type(budget) is not int or not 16<=budget<=64:
            raise Refused("fixture_budget_admission")
        self._coordinator=coordinator
        self._budget=budget
        self._owner=current_thread()
        self._pending=None
        self._confirmed=False

    def _check(self):
        if current_thread() is not self._owner or self._confirmed:
            raise Refused("fixture_budget_admission_state")

    def prepare(self,context,candidates,profile):
        self._check()
        self._pending=None
        if type(profile) is not str or profile not in ("standard","economy"):
            raise Refused("fixture_budget_profile")
        ceilings=DEFAULT_CEILINGS if profile=="standard" else ECONOMY_CEILINGS
        required=sum(ceilings)
        if required>self._budget:
            raise Refused("fixture_budget_plan_unaffordable")
        preview=self._coordinator.prepare(context,candidates)
        if (type(preview) is not ReplanPreview or preview.strict_results is not True
                or tuple(tuple(a.worker for a in w) for w in preview.plan.waves)!=(("plan",),("a","b"),("review",))):
            raise Refused("fixture_budget_plan_shape")
        offer=BudgetOffer(preview.attempt,preview.plan.digest,profile,self._budget,ceilings,required)
        self._pending=(offer,preview)
        return offer

    def confirm(self,offer,context,candidates):
        self._check()
        if self._pending is None or self._pending[0] is not offer:
            raise Refused("fixture_budget_offer_stale")
        _,preview=self._pending
        self._pending=None
        if (offer.attempt!=preview.attempt or offer.plan_digest!=preview.plan.digest
                or offer.budget!=self._budget or sum(offer.ceilings)!=offer.required
                or offer.required>self._budget):
            raise Refused("fixture_budget_offer_changed")
        ledger=TokenLedger(self._budget,strict_dispatch=True,ceilings=offer.ceilings)
        ledger.preflight(tuple(PARENTS))
        run=self._coordinator.confirm(preview,context,candidates)
        self._confirmed=True
        return run,ledger
