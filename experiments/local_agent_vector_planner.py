# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic deterministic two-dimensional local agent planner.

One trusted memory profile per candidate, no physical device measurements.
CPU versus accelerator eligibility is explicit, never an implicit fallback.
No shell, provider calls, model loading or production integration.
"""
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
from gateway import Context,Refused,token
from inference import Candidate
from local_agent_planner import Worker
from local_agent_vector_resources import Capacity,Demand,VectorResources

@dataclass(frozen=True)
class ModelMemory:
    model: str
    backend: str
    capacity: Capacity

@dataclass(frozen=True)
class VectorAssignment:
    worker: str
    model: str
    provider: str
    backend: str
    capacity: Capacity

@dataclass(frozen=True)
class VectorPlan:
    context: Context
    waves: tuple
    digest: str

def vector_plan(context,workers,candidates,memory,capacity,limit=6,preference="balanced",*,backend="accelerator"):
    if type(context) is not Context:
        raise Refused("vector_plan_context")
    context.checked()
    if context.mode=="CLOUD_CHAT":
        raise Refused("vector_plan_local_only")
    if (type(capacity) is not Capacity or type(workers) is not tuple or not 1<=len(workers)<=10
            or type(candidates) is not tuple or not 1<=len(candidates)<=64
            or type(memory) is not tuple or len(memory)!=len(candidates)
            or type(limit) is not int or not 1<=limit<=10
            or type(preference) is not str or preference not in ("balanced","economy","performance")
            or type(backend) is not str or backend not in ("cpu","accelerator","mixed")):
        raise Refused("vector_plan_input")
    capacity.checked()
    models={}
    for c in candidates:
        if type(c) is not Candidate:
            raise Refused("vector_plan_candidate")
        c.checked()
        if c.identity in models:
            raise Refused("vector_plan_duplicate")
        models[c.identity]=c
    profiles={}
    for p in memory:
        if (type(p) is not ModelMemory or type(p.model) is not str or p.model not in models
                or p.model in profiles or type(p.backend) is not str or p.backend not in ("cpu","accelerator")
                or type(p.capacity) is not Capacity):
            raise Refused("vector_plan_memory")
        p.capacity.checked()
        if (p.capacity.ram_mib<=0 or (p.backend=="cpu")!=(p.capacity.vram_mib==0)
                or p.capacity.ram_mib+p.capacity.vram_mib<models[p.model].memory_mib):
            raise Refused("vector_plan_memory")
        profiles[p.model]=p
    graph={}
    for w in workers:
        if type(w) is not Worker or type(w.identity) is not str or w.identity in graph:
            raise Refused("vector_plan_worker")
        token(w.identity)
        if (type(w.depends) is not tuple or len(w.depends)>9
                or type(w.memory_mib) is not int or not 0<=w.memory_mib<=1048576
                or type(w.role) is not str or w.role not in ("plan","execute","review")):
            raise Refused("vector_plan_worker")
        for dep in w.depends:
            if type(dep) is not str:
                raise Refused("vector_plan_dependency")
            token(dep)
        if w.identity in w.depends or len(set(w.depends))!=len(w.depends):
            raise Refused("vector_plan_dependency")
        graph[w.identity]=w
    if any(dep not in graph for w in workers for dep in w.depends):
        raise Refused("vector_plan_dependency")
    eligible=tuple(c for c in models.values() if c.task=="llm" and c.locality=="local"
                   and c.compatible and c.healthy and c.memory_mib>0
                   and (backend=="mixed" or profiles[c.identity].backend==backend))
    done=set()
    waves=[]
    while len(done)<len(graph):
        ready=tuple(w for w in sorted(workers,key=lambda x:x.identity) if w.identity not in done and set(w.depends)<=done)
        if not ready:
            raise Refused("vector_plan_cycle")
        ram,vram=capacity.ram_mib,capacity.vram_mib
        wave=[]
        for w in ready:
            if len(wave)>=limit:
                break
            options=[]
            for c in eligible:
                p=profiles[c.identity]
                need=Capacity(p.capacity.ram_mib+w.memory_mib,p.capacity.vram_mib)
                if need.ram_mib<=ram and need.vram_mib<=vram:
                    # Dominant fraction of total host capacity, not a speed benchmark.
                    load=max(Fraction(need.ram_mib,max(1,capacity.ram_mib)),
                             Fraction(need.vram_mib,max(1,capacity.vram_mib)))
                    if preference=="economy":
                        key=(load,need.vram_mib,need.ram_mib,-c.rank,c.identity)
                    elif preference=="performance":
                        key=(-c.rank,load,need.vram_mib,need.ram_mib,c.identity)
                    else:
                        key=(-Fraction(c.rank,1)/load,-c.rank,load,c.identity)
                    options.append((key,c,p,need))
            if not options:
                continue
            _,c,p,need=min(options,key=lambda x:x[0])
            wave.append(VectorAssignment(w.identity,c.identity,c.provider,p.backend,need))
            ram-=need.ram_mib
            vram-=need.vram_mib
        if not wave:
            raise Refused("vector_plan_capacity")
        waves.append(tuple(wave))
        done.update(a.worker for a in wave)
    envelope=("vector-plan-v1",context.task,context.session,context.profile,context.generation,context.route,
              context.mode,context.offline,context.public_opt_in,capacity.ram_mib,capacity.vram_mib,limit,preference,backend,
              tuple((w.identity,tuple(sorted(w.depends)),w.memory_mib,w.role) for w in sorted(workers,key=lambda x:x.identity)),
              tuple((c.identity,c.task,c.locality,c.provider,c.compatible,c.healthy,c.memory_mib,c.rank,
                     profiles[c.identity].backend,profiles[c.identity].capacity.ram_mib,profiles[c.identity].capacity.vram_mib)
                    for c in sorted(candidates,key=lambda x:x.identity)),
              tuple(tuple((a.worker,a.model,a.provider,a.backend,a.capacity.ram_mib,a.capacity.vram_mib) for a in wave) for wave in waves))
    return VectorPlan(context,tuple(waves),sha256(json.dumps(envelope,separators=(",",":")).encode("ascii")).hexdigest())
