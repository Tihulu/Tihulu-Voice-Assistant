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
from document_result import DocumentFixture

ACTIONS={"file_read":"engine","typed_engine":"document_engine","reasoning":"local_plan"}

@dataclass(frozen=True)
class DataReceipt:
    task: str
    request_digest: str
    payload_digest: str
    input_digest: str
    size: int

class DataTeamRun:
    def __init__(self,admission,plan,calls,ledger,*,memory_budget,token_budget,limit=6,document_sources=()):
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
        self._sources={}
        if type(document_sources) is not tuple or len(document_sources)>10:
            raise Refused("data_team_document_sources")
        for entry in document_sources:
            if type(entry) is not tuple or len(entry)!=2:
                raise Refused("data_team_document_source")
            task,job=entry
            if (type(task) is not str or task in self._sources or task not in self._calls
                    or self._calls[task].operation!="read_text" or type(job) is not DocumentFixture
                    or job._gateway is not self._g):
                raise Refused("data_team_document_source")
            receipt=job.result()
            data=" ".join(b.text for b in receipt[3].blocks).encode("utf-8")
            if data!=self._calls[task].data:
                raise Refused("data_team_document_bytes")
            self._g.checkpoint_claimed_group(((job._grant,job._request),))
            self._sources[task]=(job,receipt[4])
        self._claims={}
        self._results={}
        self._stopped=False
        self._generated_budget=None

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("data_team_thread")

    def generated_budget(self):
        """One synthetic generation ledger per confirmed team run."""
        self._thread()
        if self._stopped:
            raise Refused("data_team_stopped")
        if self._generated_budget is None:
            from heterogeneous_team_token_budget import TeamTokenBudget
            self._generated_budget=TeamTokenBudget(self._plan.digest,min(self._tokens,self._plan.output_ceiling))
        return self._generated_budget

    def stop(self):
        self._thread()
        self._stopped=True
        if self._generated_budget is not None:
            self._generated_budget.close()
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
            # Any already claimed task or attached document revocation stops
            # the entire team before another computation, not just its descendants.
            for job,digest in self._sources.values():
                if job.result()[4]!=digest:
                    raise Refused("data_team_document_changed")
                self._g.checkpoint_claimed_group(((job._grant,job._request),))
            if self._claims:
                self._g.checkpoint_claimed_group(tuple(self._claims.values()))
        except Refused:
            self.stop()
            raise

    def request(self,task):
        self._thread()
        if self._stopped or type(task) is not str or task not in self._calls:
            raise Refused("data_team_task")
        r=self._routes[task]
        c=self._calls[task]
        source=self._sources.get(task)
        provenance=() if source is None else (source[0]._grant.identity,source[0]._request.target,source[1])
        digest=sha256(encode(("data-team-v2",self._attempt,self._plan.digest,task,
                              r.route.channel,r.model,r.provider,r.profile_digest,c.digest(),provenance))).hexdigest()
        return Request(self._g.context,ACTIONS[r.route.channel],digest,"local","private").checked()

    def execute(self,task,grant,context,available,models):
        self._check(context,available,models)
        req=self.request(task)
        wave=next((w for w in self._plan.waves if any(r.route.agent not in self._results for r in w)),())
        if task in self._results or task not in {r.route.agent for r in wave}:
            raise Refused("data_team_wave")
        parents=self._parents[task]
        deps=tuple(self._claims[p] for p in parents)
        source=self._sources.get(task)
        if source is not None:
            deps+=((source[0]._grant,source[0]._request),)
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
            self._g.checkpoint_claimed_group(tuple(self._claims.values()))
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
