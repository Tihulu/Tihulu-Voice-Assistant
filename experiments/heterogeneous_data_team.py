# SPDX-License-Identifier: AGPL-3.0-only
"""Exact local synchronous data-tool team fixture. No actual model or OS effects."""
from dataclasses import dataclass
from hashlib import sha256
from threading import current_thread
from gateway import Refused,Request
from heterogeneous_team import plan_team,TeamPlan
from heterogeneous_team_admission import TeamAdmission
from heterogeneous_data_tools import DataCall,OPERATIONS,evaluate,encode
from local_agent_resources import ResourceLedger

ACTIONS={"file_read":"engine","typed_engine":"document_engine","reasoning":"local_plan"}

@dataclass(frozen=True)
class DataReceipt:
    task: str
    request_digest: str
    payload_digest: str
    input_digest: str
    size: int

class DataTeamRun:
    def __init__(self,admission,plan,calls,ledger,*,memory_budget,token_budget,limit=6):
        if (type(admission) is not TeamAdmission or type(plan) is not TeamPlan
                or type(ledger) is not ResourceLedger or type(calls) is not tuple
                or not 1<=len(calls)<=10):
            raise Refused("data_team_input")
        self._owner=current_thread()
        self._g=admission._g
        self._plan,self._attempt,tasks=admission.take_confirmed(plan)
        self._tasks=tasks
        self._ledger=ledger
        self._budget=memory_budget
        self._tokens=token_budget
        self._limit=limit
        self._routes={r.route.agent:r for w in plan.waves for r in w}
        self._calls={}
        self._parents={t.identity:tuple(sorted(t.depends)) for t in tasks}
        for c in calls:
            if type(c) is not DataCall:
                raise Refused("data_team_call")
            c.checked()
            r=self._routes.get(c.task)
            if (r is None or c.task in self._calls or r.route.locality!="local"
                    or r.route.channel!=OPERATIONS[c.operation]
                    or bool(self._parents[c.task])!=(c.operation=="report")):
                raise Refused("data_team_call_scope")
            self._calls[c.task]=c
        if set(self._calls)!=set(self._routes):
            raise Refused("data_team_call_coverage")
        self._claims={}
        self._results={}
        self._stopped=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("data_team_thread")

    def stop(self):
        self._thread()
        self._stopped=True
        for grant,req in self._claims.values():
            self._g.revoke(grant.identity)
        self._results.clear()

    def _check(self,context,available,models):
        self._thread()
        if self._stopped:
            raise Refused("data_team_stopped")
        try:
            if context!=self._g.context or plan_team(context,self._tasks,available,models,self._budget,
                    self._tokens,limit=self._limit)!=self._plan:
                raise Refused("data_team_plan_changed")
        except Refused:
            self.stop()
            raise

    def request(self,task):
        self._thread()
        if self._stopped or type(task) is not str or task not in self._calls:
            raise Refused("data_team_task")
        r=self._routes[task]
        c=self._calls[task]
        digest=sha256(encode(("data-team-v1",self._attempt,self._plan.digest,task,
                              r.route.channel,r.model,r.provider,r.profile_digest,c.digest()))).hexdigest()
        return Request(self._g.context,ACTIONS[r.route.channel],digest,"local","private").checked()

    def execute(self,task,grant,context,available,models):
        self._check(context,available,models)
        req=self.request(task)
        wave=next((w for w in self._plan.waves if any(r.route.agent not in self._results for r in w)),())
        if task in self._results or task not in {r.route.agent for r in wave}:
            raise Refused("data_team_wave")
        parents=self._parents[task]
        deps=tuple(self._claims[p] for p in parents)
        rows=tuple((p,self._results[p][1]) for p in parents)
        inputs=tuple((p,self._results[p][0].request_digest,self._results[p][0].payload_digest,
                      self._results[p][0].input_digest) for p in parents)
        reservation=self._ledger.reserve(self._attempt,task,self._routes[task].memory_mib)
        try:
            if deps:
                self._g.claim_with_dependencies(grant,req,deps)
            else:
                self._g.claim(grant,req)
            self._claims[task]=(grant,req)
            self._g.checkpoint_claimed_group(((grant,req),))
            payload=evaluate(self._calls[task],rows)
            self._g.checkpoint_claimed_group(((grant,req),))
            receipt=DataReceipt(task,req.target,sha256(payload).hexdigest(),
                         sha256(encode(("data-team-input-v1",req.target,inputs))).hexdigest(),len(payload))
            self._results[task]=(receipt,payload)
            return receipt
        except BaseException:
            self.stop()
            raise
        finally:
            # Synchronous fixed computation has returned or raised; no subprocess.
            self._ledger.release(reservation)

    def output(self,task,context,available,models):
        self._check(context,available,models)
        if type(task) is not str or task not in self._results or len(self._results)!=len(self._calls):
            raise Refused("data_team_incomplete")
        try:
            self._g.checkpoint_claimed_group(tuple(self._claims.values()))
        except Refused:
            self.stop()
            raise
        return self._results[task]
