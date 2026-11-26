# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping local agent planning, no spawn, network, shell or inference."""
from dataclasses import dataclass
from hashlib import sha256
from fractions import Fraction
import json
from gateway import Context,Refused,token
from inference import Candidate

@dataclass(frozen=True)
class Worker:
    identity: str
    depends: tuple
    memory_mib: int
    role: str

@dataclass(frozen=True)
class Assignment:
    worker: str
    model: str
    provider: str
    memory_mib: int

@dataclass(frozen=True)
class AgentPlan:
    context: Context
    waves: tuple
    digest: str

def plan_agents(context,workers,candidates,budget,limit=6,preference="balanced"):
    """Conservative simultaneous per-worker model memory, no sharing assumed.
    All executor/planner assignments local; cloud planning is a separate
    approved proposal path, not an executor. Inputs are trusted fixture
    inventory and task graph, not model-generated commands.
    """
    if type(context) is not Context:
        raise Refused("invalid_context")
    context.checked()
    if context.mode=="CLOUD_CHAT":
        raise Refused("local_agent_mode_denied")
    if (type(workers) is not tuple or not 1<=len(workers)<=10
            or type(candidates) is not tuple or not 1<=len(candidates)<=64
            or type(budget) is not int or not 1<=budget<=1048576
            or type(limit) is not int or not 1<=limit<=10
            or type(preference) is not str or preference not in ("balanced","performance","economy")):
        raise Refused("invalid_agent_plan")
    models={}
    for c in candidates:
        if type(c) is not Candidate:
            raise Refused("invalid_candidate")
        c.checked()
        if c.identity in models:
            raise Refused("duplicate_candidate")
        models[c.identity]=c
    eligible=tuple(c for c in models.values() if c.task=="llm" and c.locality=="local"
                   and c.compatible and c.healthy and c.memory_mib>0)
    graph={}
    for w in workers:
        if type(w) is not Worker:
            raise Refused("invalid_worker")
        token(w.identity)
        if (w.identity in graph or type(w.depends) is not tuple or len(w.depends)>9
                or type(w.memory_mib) is not int or not 0<=w.memory_mib<=budget
                or type(w.role) is not str or w.role not in ("plan","execute","review")):
            raise Refused("invalid_worker")
        for d in w.depends:
            token(d)
        if len(set(w.depends))!=len(w.depends) or w.identity in w.depends:
            raise Refused("invalid_worker_dependency")
        graph[w.identity]=w
    if any(d not in graph for w in workers for d in w.depends):
        raise Refused("unknown_worker_dependency")
    # Validate DAG before any schedule. No partial plan for cycles.
    done=set()
    while len(done)<len(graph):
        ready={w.identity for w in workers if w.identity not in done and set(w.depends)<=done}
        if not ready:
            raise Refused("agent_dependency_cycle")
        done.update(ready)
    done=set()
    waves=[]
    while len(done)<len(graph):
        wave=[]
        available=budget
        for w in sorted(workers,key=lambda x:x.identity):
            if w.identity in done or not set(w.depends)<=done:
                continue
            options=[c for c in eligible if c.memory_mib+w.memory_mib<=available]
            if not options or len(wave)>=limit:
                continue
            if preference=="economy":
                key=lambda c:(c.memory_mib,-c.rank,c.identity)
            elif preference=="performance":
                key=lambda c:(-c.rank,c.memory_mib,c.identity)
            else:
                # Rank per reserved MiB, with deterministic tie-breaking.
                # This is a policy heuristic, not a measured quality score.
                key=lambda c:(-Fraction(c.rank,c.memory_mib+w.memory_mib),-c.rank,c.memory_mib,c.identity)
            model=min(options,key=key)
            need=model.memory_mib+w.memory_mib
            wave.append(Assignment(w.identity,model.identity,model.provider,need))
            available-=need
        if not wave:
            raise Refused("agent_resource_unavailable")
        waves.append(tuple(wave))
        done.update(a.worker for a in wave)
    envelope=("local-agent-plan-v2",(context.task,context.session,context.profile,
              context.generation,context.route,context.mode,context.offline,context.public_opt_in),
              budget,limit,preference,
              tuple((w.identity,tuple(sorted(w.depends)),w.memory_mib,w.role) for w in sorted(workers,key=lambda x:x.identity)),
              tuple((c.identity,c.task,c.locality,c.provider,c.compatible,c.healthy,c.memory_mib,c.rank)
                    for c in sorted(candidates,key=lambda x:x.identity)),
              tuple(tuple((a.worker,a.model,a.provider,a.memory_mib) for a in wave) for wave in waves))
    digest=sha256(json.dumps(envelope,separators=(",",":")).encode("ascii")).hexdigest()
    return AgentPlan(context,tuple(waves),digest)
