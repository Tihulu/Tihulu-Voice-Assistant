# SPDX-License-Identifier: AGPL-3.0-only
"""One-shot confirmation of heterogeneous team plan. No effect authorization."""
from dataclasses import dataclass
from threading import current_thread
from gateway import Context,FixtureGateway,Refused
from agent_custom_profiles import ident
from heterogeneous_team import TeamPlan,plan_team

@dataclass(frozen=True,eq=False)
class TeamOffer:
    identity: str
    plan: TeamPlan

class TeamAdmission:
    def __init__(self,gateway,identity):
        if type(gateway) is not FixtureGateway:
            raise Refused("team_admission_gateway")
        self._g=gateway
        self._identity=ident(identity)
        gateway.register_agent_attempt("team-admission:"+self._identity)
        self._owner=current_thread()
        self._pending=None
        self._issued=0
        self._closed=False

    def _check(self,context):
        if current_thread() is not self._owner or self._closed:
            raise Refused("team_admission_closed")
        if type(context) is not Context or context!=self._g.context:
            self.close()
            raise Refused("team_admission_context")

    def prepare(self,context,tasks,available,models,memory_budget,token_budget,*,limit=6,approved_cloud=()):
        self._check(context)
        self._pending=None
        if self._issued>=16:
            self.close()
            raise Refused("team_admission_limit")
        plan=plan_team(context,tasks,available,models,memory_budget,token_budget,
                       limit=limit,approved_cloud=approved_cloud)
        self._issued+=1
        offer=TeamOffer(self._identity+":"+str(self._issued),plan)
        self._pending=offer
        return offer

    def confirm(self,offer,context,tasks,available,models,memory_budget,token_budget,*,limit=6,approved_cloud=()):
        self._check(context)
        if type(offer) is not TeamOffer or offer is not self._pending:
            raise Refused("team_offer_stale")
        # Consume before fresh validation, even if the recheck fails.
        self._pending=None
        fresh=plan_team(context,tasks,available,models,memory_budget,token_budget,
                        limit=limit,approved_cloud=approved_cloud)
        if fresh!=offer.plan:
            raise Refused("team_plan_changed")
        self._g.register_agent_attempt("team-confirmed:"+offer.identity)
        self._closed=True
        return fresh

    def close(self):
        if current_thread() is not self._owner:
            raise Refused("team_admission_thread")
        self._pending=None
        self._closed=True
