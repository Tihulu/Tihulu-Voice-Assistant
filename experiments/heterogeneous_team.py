# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping heterogeneous team planner. No dispatch or grants."""
from dataclasses import dataclass,asdict
from hashlib import sha256
import json
from gateway import Context,Refused
from agent_custom_profiles import AgentProfile,ident
from agent_model_capability import ModelOffer,select
from heterogeneous_agents import ORDER

# Until verified isolated workspace scopes exist, assume a shared desktop.
DESKTOP_READ=frozenset(("screen_observe","accessibility_read"))
DESKTOP_ALL=DESKTOP_READ|{"desktop_input"}

@dataclass(frozen=True)
class TeamTask:
    identity: str
    profile: AgentProfile
    alternatives: tuple
    depends: tuple=()

@dataclass(frozen=True)
class TeamPlan:
    context: Context
    waves: tuple
    output_ceiling: int
    digest: str

def plan_team(context,tasks,available,models,memory_budget,token_budget,*,limit=6,approved_cloud=()):
    if type(context) is not Context:
        raise Refused("team_context")
    context.checked()
    if (type(tasks) is not tuple or not 1<=len(tasks)<=10
            or type(memory_budget) is not int or not 1<=memory_budget<=1048576
            or type(token_budget) is not int or not 1<=token_budget<=655360
            or type(limit) is not int or not 1<=limit<=10
            or type(available) is not tuple or not 1<=len(available)<=len(ORDER)
            or any(type(c) is not str or c not in ORDER for c in available)
            or len(set(available))!=len(available)
            or type(models) is not tuple or not 1<=len(models)<=64
            or type(approved_cloud) is not tuple or len(approved_cloud)>32):
        raise Refused("team_input")
    for p in approved_cloud:
        ident(p)
    if len(set(approved_cloud))!=len(approved_cloud):
        raise Refused("team_cloud_duplicate")
    seen=set()
    for m in models:
        if type(m) is not ModelOffer:
            raise Refused("team_model")
        m.checked()
        if m.identity in seen:
            raise Refused("team_model_duplicate")
        seen.add(m.identity)
    graph={}
    profiles={}
    for t in tasks:
        if type(t) is not TeamTask or type(t.profile) is not AgentProfile:
            raise Refused("team_task")
        ident(t.identity)
        t.profile.spec(t.identity,t.alternatives)
        if (t.identity in graph or type(t.depends) is not tuple or len(t.depends)>9):
            raise Refused("team_dependency")
        for d in t.depends:
            ident(d)
        if len(set(t.depends))!=len(t.depends) or t.identity in t.depends:
            raise Refused("team_dependency")
        pd=t.profile.digest()
        if t.profile.identity in profiles and profiles[t.profile.identity]!=pd:
            raise Refused("team_profile_conflict")
        profiles[t.profile.identity]=pd
        graph[t.identity]=t
    if any(d not in graph for t in tasks for d in t.depends):
        raise Refused("team_unknown_dependency")
    ceiling=sum(t.profile.output_tokens for t in tasks)
    if ceiling>token_budget:
        raise Refused("team_token_budget")
    # Validate graph and individual feasibility before producing any waves.
    done=set()
    while len(done)<len(graph):
        ready={t.identity for t in tasks if t.identity not in done and set(t.depends)<=done}
        if not ready:
            raise Refused("team_cycle")
        done.update(ready)
    # Cloud reasoning is a separate root advice proposal. A private upstream
    # result cannot become cloud input merely because a dependency exists.
    for t in tasks:
        if t.profile.locality=="cloud" and t.depends:
            raise Refused("team_cloud_dependency")
    initial={t.identity:select(context,t.profile,t.identity,t.alternatives,available,models,
                                memory_budget,approved_cloud=approved_cloud) for t in tasks}
    done=set()
    waves=[]
    while len(done)<len(graph):
        wave=[]
        remaining=memory_budget
        channels=set()
        for t in sorted(tasks,key=lambda x:x.identity):
            if t.identity in done or not set(t.depends)<=done or len(wave)>=limit:
                continue
            # Do not change the chosen model/interface because a peer uses RAM.
            r=initial[t.identity]
            channel=r.route.channel
            conflict=(channel=="desktop_input" and bool(channels&DESKTOP_ALL)) or (channel in DESKTOP_READ and "desktop_input" in channels)
            if r.memory_mib>remaining or conflict:
                continue
            wave.append(r)
            remaining-=r.memory_mib
            channels.add(channel)
        if not wave:
            raise Refused("team_resource_unavailable")
        waves.append(tuple(wave))
        done.update(r.route.agent for r in wave)
    canonical=("heterogeneous-team-v2",asdict(context),memory_budget,token_budget,limit,
               sorted(available),sorted(approved_cloud),
               [(t.identity,t.profile.digest(),sorted(t.alternatives),sorted(t.depends))
                for t in sorted(tasks,key=lambda x:x.identity)],
               [dict(asdict(m),capabilities=sorted(m.capabilities)) for m in sorted(models,key=lambda x:x.identity)],
               [[asdict(r) for r in w] for w in waves])
    digest=sha256(json.dumps(canonical,sort_keys=True,separators=(",",":")).encode("ascii")).hexdigest()
    return TeamPlan(context,tuple(waves),ceiling,digest)
