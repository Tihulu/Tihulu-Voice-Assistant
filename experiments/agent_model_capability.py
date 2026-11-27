# SPDX-License-Identifier: AGPL-3.0-only
"""Capability-aware model/channel proposal selection, not detection or execution."""
from dataclasses import dataclass
from fractions import Fraction
from gateway import Context,Refused
from agent_custom_profiles import AgentProfile,ident
from heterogeneous_agents import ORDER,plan_route

# Model capability differs from the permissions of the tool it proposes.
CAPABILITIES=frozenset(("text","vision","structured_tools","speech_recognition","speech_synthesis","gui_grounding"))
REQUIRED={c:("text","structured_tools") for c in ORDER}
REQUIRED.update(reasoning=("text",),image_review=("text","vision"),
                screen_observe=("text","vision"),desktop_input=("text","vision","gui_grounding","structured_tools"),
                voice_input=("speech_recognition",),voice_output=("speech_synthesis",))

@dataclass(frozen=True)
class ModelOffer:
    identity: str
    provider: str
    locality: str
    capabilities: tuple
    memory_mib: int
    rank: int
    tested: bool
    healthy: bool

    def checked(self):
        ident(self.identity)
        ident(self.provider)
        if (type(self.locality) is not str or self.locality not in ("local","cloud")
                or type(self.capabilities) is not tuple or not 1<=len(self.capabilities)<=len(CAPABILITIES)
                or any(type(c) is not str or c not in CAPABILITIES for c in self.capabilities)
                or len(set(self.capabilities))!=len(self.capabilities)
                or type(self.memory_mib) is not int or not 0<=self.memory_mib<=1048576
                or (self.locality=="local" and self.memory_mib==0)
                or (self.locality=="cloud" and self.memory_mib!=0)
                or type(self.rank) is not int or not 0<=self.rank<=100
                or type(self.tested) is not bool or type(self.healthy) is not bool):
            raise Refused("agent_model_offer")
        return self

@dataclass(frozen=True)
class ModelRoute:
    route: object
    model: str
    provider: str
    memory_mib: int
    output_tokens: int
    profile_digest: str

def select(context,profile,agent,alternatives,available,models,budget,*,approved_cloud=()):
    if type(context) is not Context or type(profile) is not AgentProfile:
        raise Refused("agent_model_context")
    profile.checked()
    spec=profile.spec(agent,alternatives)
    if (type(models) is not tuple or not 1<=len(models)<=64
            or type(budget) is not int or not 0<=budget<=1048576
            or type(approved_cloud) is not tuple or len(approved_cloud)>32
            or type(available) is not tuple):
        raise Refused("agent_model_inventory")
    for p in approved_cloud:
        ident(p)
    seen=set()
    pool=[]
    for m in models:
        if type(m) is not ModelOffer:
            raise Refused("agent_model_inventory")
        m.checked()
        if m.identity in seen:
            raise Refused("agent_model_duplicate")
        seen.add(m.identity)
        need=m.memory_mib+profile.memory_mib
        if (m.locality==profile.locality and m.tested and m.healthy and need<=budget
                and (not profile.model or m.identity==profile.model)
                and (not profile.provider or m.provider==profile.provider)
                and (m.locality=="local" or m.provider in approved_cloud)):
            pool.append(m)
    # First select the eligible interface, without implicitly broadening alternatives.
    possible=tuple(c for c in available if type(c) is str and c in REQUIRED
                   and any(set(REQUIRED[c])<=set(m.capabilities) for m in pool))
    # Validate full advertised channel inventory before filtering.
    plan_route(context,spec,available)
    route=plan_route(context,spec,possible)
    eligible=[m for m in pool if set(REQUIRED[route.channel])<=set(m.capabilities)]
    if profile.preference=="economy":
        key=lambda m:(m.memory_mib,-m.rank,m.identity)
    elif profile.preference=="performance":
        key=lambda m:(-m.rank,m.memory_mib,m.identity)
    else:
        key=lambda m:(-Fraction(m.rank,max(1,m.memory_mib+profile.memory_mib)),-m.rank,m.memory_mib,m.identity)
    m=min(eligible,key=key)
    return ModelRoute(route,m.identity,m.provider,m.memory_mib+profile.memory_mib,
                      profile.output_tokens,profile.digest())
