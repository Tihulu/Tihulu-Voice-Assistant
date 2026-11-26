# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping agent wave lifecycle; fixture grants only, no effects."""
from hashlib import sha256
from dataclasses import dataclass
from gateway import token
from threading import current_thread
from gateway import Context,FixtureGateway,Request,Refused
from local_agent_planner import AgentPlan,plan_agents
from local_agent_resources import ResourceLedger

MAX_RESULT_BYTES=16384

@dataclass(frozen=True)
class AgentResult:
    worker: str
    plan_digest: str
    grant_identity: str
    request_target: str
    payload_digest: str
    size: int

class WaveRun:
    """Trusted coordinator supplies current snapshots at every checkpoint.
    No caller-supplied callbacks, executor, subprocess or provider transport.
    A wave barrier prevents concurrent reservations from crossing waves.
    """
    def __init__(self,gateway,plan,workers,candidates,budget,limit=6,preference="balanced",*,ledger=None,run_id=None,attempt=None):
        if type(gateway) is not FixtureGateway or type(plan) is not AgentPlan:
            raise Refused("agent_run_invalid")
        self._gateway=gateway
        self._plan=plan
        self._workers=workers
        self._budget=budget
        self._limit=limit
        self._preference=preference
        self._owner=current_thread()
        if ledger is not None and (type(ledger) is not ResourceLedger or type(run_id) is not str):
            raise Refused("agent_resource_ledger_invalid")
        if ledger is None and run_id is not None:
            raise Refused("agent_resource_ledger_invalid")
        if attempt is not None:
            if type(attempt) is not str:
                raise Refused("agent_attempt_invalid")
            token(attempt)
        self._attempt=attempt
        self._ledger=ledger
        self._run_id=run_id
        self._reservations={}
        self._outputs={}
        self._claims={}
        self._done=set()
        self._cancelled=False
        self._check(gateway.context,candidates)
        if self._attempt is not None:
            gateway.register_agent_attempt(self._attempt)

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("agent_run_thread")

    def _check(self,context,candidates):
        self._thread()
        if self._cancelled:
            raise Refused("agent_run_cancelled")
        try:
            if type(context) is not Context or context!=self._gateway.context:
                raise Refused("agent_context_changed")
            fresh=plan_agents(context,self._workers,candidates,self._budget,self._limit,self._preference)
            if fresh!=self._plan:
                raise Refused("agent_plan_changed")
        except Refused:
            # Observed stale context/inventory is terminal, even if the old
            # snapshot is later supplied again. No automatic replan.
            self.cancel()
            raise

    def _wave(self):
        return next((w for w in self._plan.waves if any(a.worker not in self._done for a in w)),())

    def request(self,worker):
        self._thread()
        if type(worker) is not str:
            raise Refused("agent_worker_unknown")
        token(worker)
        for wave in self._plan.waves:
            for a in wave:
                if a.worker==worker:
                    fields=("agent-wave-v1",self._plan.digest,a.worker,a.model,
                            a.provider,a.memory_mib)
                    if self._attempt is not None:
                        fields=("agent-wave-attempt-v1",self._attempt,self._run_id)+fields[1:]
                    target=sha256(repr(fields).encode("ascii")).hexdigest()
                    return Request(self._plan.context,"agent",target,"local","private").checked()
        raise Refused("agent_worker_unknown")

    def start(self,worker,grant,context,candidates):
        self._check(context,candidates)
        if type(worker) is not str or worker in self._claims or not any(a.worker==worker for a in self._wave()):
            raise Refused("agent_wave_not_ready")
        req=self.request(worker)
        # Prior waves are complete. Bind the entire earlier wave barrier
        # (including transitive grant dependencies) before claiming a child.
        current_wave=self._wave()
        previous=[]
        for wave in self._plan.waves:
            if wave is current_wave:
                break
            previous.extend(self._claims[a.worker] for a in wave)
        parents=tuple(previous)
        reservation=None
        if self._ledger is not None:
            assignment=next(a for a in current_wave if a.worker==worker)
            reservation=self._ledger.reserve(self._run_id,worker,assignment.memory_mib)
        try:
            if parents:
                self._gateway.claim_with_dependencies(grant,req,parents)
            else:
                self._gateway.claim(grant,req)
        except BaseException:
            if reservation is not None:
                self._ledger.release(reservation)
            raise
        self._claims[worker]=(grant,req)
        if reservation is not None:
            self._reservations[worker]=reservation

    def _event_owner(self,worker,completion_grant):
        self._thread()
        if self._attempt is not None:
            if (type(worker) is not str or worker not in self._claims
                    or worker in self._done or completion_grant is not self._claims[worker][0]):
                raise Refused("agent_completion_attempt_mismatch")

    def complete(self,worker,context,candidates,*,payload=None,completion_grant=None):
        # A foreign/late event must not invalidate a newer attempt with a
        # stale context before its exact ownership has been established.
        self._event_owner(worker,completion_grant)
        self._check(context,candidates)
        if type(worker) is not str or worker not in self._claims or worker in self._done:
            raise Refused("agent_worker_not_running")
        if payload is not None and (type(payload) is not bytes or not 0<len(payload)<=MAX_RESULT_BYTES):
            raise Refused("agent_result_invalid")
        grant,req=self._claims[worker]
        self._gateway.checkpoint_claimed_group(((grant,req),))
        # In this synthetic API completion means the simulated worker ended.
        self._release(worker)
        if payload is not None:
            receipt=AgentResult(worker,self._plan.digest,grant.identity,req.target,
                                sha256(payload).hexdigest(),len(payload))
            self._outputs[worker]=(receipt,payload)
        self._done.add(worker)

    def inputs(self,worker,context,candidates):
        """Return exact direct dependency receipts, only to a running child.
        No interpretation of result bytes as commands or provider messages.
        """
        self._check(context,candidates)
        if type(worker) is not str or worker not in self._claims or worker in self._done:
            raise Refused("agent_worker_not_running")
        parents=next(w.depends for w in self._workers if w.identity==worker)
        if any(parent not in self._outputs for parent in parents):
            raise Refused("agent_dependency_result_missing")
        claims=(self._claims[worker],)+tuple(self._claims[p] for p in sorted(parents))
        self._gateway.checkpoint_claimed_group(claims)
        return tuple(self._outputs[p] for p in sorted(parents))

    def output(self,worker,context,candidates):
        """Read one terminal fixture result only after the whole run is valid."""
        self.result(context,candidates)
        if type(worker) is not str or worker not in self._outputs:
            raise Refused("agent_result_missing")
        return self._outputs[worker]

    def _release(self,worker):
        reservation=self._reservations.get(worker)
        if reservation is not None:
            self._ledger.release(reservation)
            del self._reservations[worker]

    def fail(self,worker,context,candidates,*,completion_grant=None):
        """Trusted simulated terminal failure; no automatic fallback/retry."""
        self._event_owner(worker,completion_grant)
        self._check(context,candidates)
        if type(worker) is not str or worker not in self._claims or worker in self._done:
            raise Refused("agent_worker_not_running")
        self.cancel()

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
            self._outputs.clear()
            for grant,req in self._claims.values():
                self._gateway.revoke(grant.identity)
            # Only synthetic runs: no actual process is alive. A real broker
            # must keep reservations until owned process termination is proven.
            for worker in tuple(self._reservations):
                self._release(worker)
