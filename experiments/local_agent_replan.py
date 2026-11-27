# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit fresh-attempt replanning for trusted synthetic coordinator only."""
from dataclasses import dataclass
from threading import current_thread
from gateway import Context,FixtureGateway,Refused,token
from local_agent_planner import AgentPlan,plan_agents
from local_agent_resources import ResourceLedger
from local_agent_wave_run import WaveRun
from local_agent_owned_drain import ManagedWave

@dataclass(frozen=True,eq=False)
class ReplanPreview:
    attempt: str
    plan: AgentPlan
    strict_results: bool = False

class AttemptCoordinator:
    """One active run at a time; no automatic retry or previous output reuse.
    The trusted owner supplies a unique coordinator identity and snapshots.
    Context changes require a new owner-created gateway/coordinator.
    """
    def __init__(self,gateway,ledger,identity,workers,budget,limit=6,
                 preference="balanced",*,max_attempts=3,owned_drain=False,strict_results=False):
        if (type(gateway) is not FixtureGateway or type(ledger) is not ResourceLedger
                or type(identity) is not str or len(identity)>96
                or type(max_attempts) is not int or not 1<=max_attempts<=3
                or type(owned_drain) is not bool or type(strict_results) is not bool
                or (strict_results and not owned_drain)):
            raise Refused("agent_coordinator_invalid")
        token(identity)
        self._gateway=gateway
        self._ledger=ledger
        self._identity=identity
        self._workers=workers
        self._budget=budget
        self._limit=limit
        self._preference=preference
        self._max_attempts=max_attempts
        self._owned_drain=owned_drain
        self._strict_results=strict_results
        self._owner=current_thread()
        self._issued=0
        self._accepted=0
        self._pending=None
        self._active=None
        self._closed=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("agent_coordinator_thread")

    def _check(self,context):
        self._thread()
        if self._closed:
            raise Refused("agent_coordinator_closed")
        if type(context) is not Context or context!=self._gateway.context:
            self.close()
            raise Refused("agent_context_changed")

    def prepare(self,context,candidates):
        self._check(context)
        self._pending=None
        if self._active is not None:
            if self._owned_drain and self._active.drained():
                self._active=None
            else:
                raise Refused("agent_attempt_active")
        if self._accepted>=self._max_attempts or self._issued>=64:
            raise Refused("agent_attempt_limit")
        plan=plan_agents(context,self._workers,candidates,self._budget,self._limit,self._preference)
        self._issued+=1
        self._pending=ReplanPreview(self._identity+"-"+str(self._issued),plan,self._strict_results)
        return self._pending

    def confirm(self,preview,context,candidates):
        """Identity is a display binding; caller must obtain actual owner intent."""
        self._check(context)
        if self._pending is None or preview is not self._pending:
            raise Refused("agent_replan_preview_stale")
        self._pending=None
        if self._active is not None or self._accepted>=self._max_attempts:
            raise Refused("agent_attempt_unavailable")
        fresh=plan_agents(context,self._workers,candidates,self._budget,self._limit,self._preference)
        if fresh!=preview.plan or preview.strict_results is not self._strict_results:
            raise Refused("agent_replan_changed")
        if self._owned_drain:
            self._active=ManagedWave(self._gateway,fresh,self._workers,candidates,
                                     self._budget,self._ledger,preview.attempt,
                                     self._limit,self._preference,strict_results=self._strict_results)
        else:
            self._active=WaveRun(self._gateway,fresh,self._workers,candidates,
                                 self._budget,self._limit,self._preference,
                                 ledger=self._ledger,run_id=preview.attempt,attempt=preview.attempt)
        self._accepted+=1
        return self._active

    def stop(self):
        """Explicitly cancel and discard current synthetic attempt before replan."""
        self._thread()
        self._pending=None
        if self._active is not None:
            if self._owned_drain:
                handles=self._active.stop()
                if handles:
                    return handles
            else:
                self._active.cancel()
            self._active=None
        return ()

    def close(self):
        self._thread()
        self.stop()
        self._closed=True
