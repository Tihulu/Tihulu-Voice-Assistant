# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping agent wave lifecycle; fixture grants only, no effects."""
from hashlib import sha256
from threading import current_thread
from gateway import Context,FixtureGateway,Request,Refused
from local_agent_planner import AgentPlan,plan_agents

class WaveRun:
    """Trusted coordinator supplies current snapshots at every checkpoint.
    No caller-supplied callbacks, executor, subprocess or provider transport.
    A wave barrier prevents concurrent reservations from crossing waves.
    """
    def __init__(self,gateway,plan,workers,candidates,budget,limit=6,preference="balanced"):
        if type(gateway) is not FixtureGateway or type(plan) is not AgentPlan:
            raise Refused("agent_run_invalid")
        self._gateway=gateway
        self._plan=plan
        self._workers=workers
        self._budget=budget
        self._limit=limit
        self._preference=preference
        self._owner=current_thread()
        self._claims={}
        self._done=set()
        self._cancelled=False
        self._check(gateway.context,candidates)

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("agent_run_thread")

    def _check(self,context,candidates):
        self._thread()
        if self._cancelled:
            raise Refused("agent_run_cancelled")
        if type(context) is not Context or context!=self._gateway.context:
            raise Refused("agent_context_changed")
        fresh=plan_agents(context,self._workers,candidates,self._budget,self._limit,self._preference)
        if fresh!=self._plan:
            raise Refused("agent_plan_changed")

    def _wave(self):
        return next((w for w in self._plan.waves if any(a.worker not in self._done for a in w)),())

    def request(self,worker):
        self._thread()
        for wave in self._plan.waves:
            for a in wave:
                if a.worker==worker:
                    target=sha256(repr(("agent-wave-v1",self._plan.digest,a.worker,a.model,
                                       a.provider,a.memory_mib)).encode("ascii")).hexdigest()
                    return Request(self._plan.context,"agent",target,"local","private").checked()
        raise Refused("agent_worker_unknown")

    def start(self,worker,grant,context,candidates):
        self._check(context,candidates)
        if worker in self._claims or not any(a.worker==worker for a in self._wave()):
            raise Refused("agent_wave_not_ready")
        req=self.request(worker)
        # Prior waves are complete. Bind the entire earlier wave barrier
        # (including transitive grant dependencies) before claiming a child.
        parents=tuple(self._claims[a.worker] for wave in self._plan.waves
                      for a in wave if a.worker in self._done)
        if parents:
            self._gateway.claim_with_dependencies(grant,req,parents)
        else:
            self._gateway.claim(grant,req)
        self._claims[worker]=(grant,req)

    def complete(self,worker,context,candidates):
        self._check(context,candidates)
        if worker not in self._claims or worker in self._done:
            raise Refused("agent_worker_not_running")
        self._gateway.checkpoint_claimed_group((self._claims[worker],))
        self._done.add(worker)

    def result(self,context,candidates):
        self._check(context,candidates)
        if self._wave():
            raise Refused("agent_run_incomplete")
        self._gateway.checkpoint_claimed_group(tuple(self._claims.values()))
        return tuple(a.worker for wave in self._plan.waves for a in wave)

    def cancel(self):
        self._thread()
        if not self._cancelled:
            self._cancelled=True
            for grant,req in self._claims.values():
                self._gateway.revoke(grant.identity)
