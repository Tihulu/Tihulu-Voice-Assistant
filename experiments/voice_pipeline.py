# SPDX-License-Identifier: AGPL-3.0-only
"""Offline voice-pipeline planning only; no model loading or execution."""
from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
from gateway import Context, Refused, token
from inference import Candidate

STAGES=("stt","llm","tts")

@dataclass(frozen=True)
class VoicePlan:
    context: Context
    models: tuple
    strategy: str
    residency: str
    budget_mib: int
    reserve_mib: int
    peak_mib: int
    pinned: tuple
    digest: str

def plan_voice(context,candidates,budget_mib,*,reserve_mib=0,
               strategy="balanced",residency="resident",pinned=()):
    """Three local stages, explicit pinning, deterministic bounded optimization.
    Sequential peak assumes verified unload between stages; not runtime proof.
    No cloud fallback, no private audio disclosure, no authorization issued.
    """
    if type(context) is not Context:
        raise Refused("invalid_context")
    context.checked()
    if context.mode=="CLOUD_CHAT":
        raise Refused("local_voice_route_required")
    if (type(candidates) is not tuple or not 3<=len(candidates)<=64
            or type(budget_mib) is not int or not 1<=budget_mib<=1048576
            or type(reserve_mib) is not int or not 0<=reserve_mib<budget_mib
            or strategy not in ("balanced","performance","memory")
            or residency not in ("resident","sequential")
            or type(pinned) is not tuple or len(pinned)>3):
        raise Refused("invalid_pipeline_plan")
    pins={}
    for pair in pinned:
        if type(pair) is not tuple or len(pair)!=2:
            raise Refused("invalid_pin")
        stage,identity=pair
        if stage not in STAGES or stage in pins:
            raise Refused("invalid_pin")
        pins[stage]=token(identity)
    seen=set()
    choices={stage:[] for stage in STAGES}
    for c in candidates:
        if type(c) is not Candidate:
            raise Refused("invalid_candidate")
        c.checked()
        if c.identity in seen:
            raise Refused("duplicate_candidate")
        seen.add(c.identity)
        if (c.locality=="local" and c.compatible and c.healthy
                and (c.task not in pins or c.identity==pins[c.task])):
            choices[c.task].append(c)
    if any(not choices[s] for s in STAGES):
        raise Refused("missing_local_stage")
    winner=None
    for models in product(*(choices[s] for s in STAGES)):
        mem=tuple(c.memory_mib for c in models)
        peak=(sum(mem) if residency=="resident" else max(mem))+reserve_mib
        if peak>budget_mib:
            continue
        score=sum(c.rank for c in models)
        weakest=min(c.rank for c in models)
        identities=tuple(c.identity for c in models)
        # Balanced maximizes weakest stage, performance total rank,
        # memory minimizes modeled peak. Stable tie-breaks, no random choice.
        key=({"balanced":(-weakest,-score,peak),
              "performance":(-score,-weakest,peak),
              "memory":(peak,-score,-weakest)}[strategy],identities)
        if winner is None or key<winner[0]:
            winner=(key,models,peak)
    if winner is None:
        raise Refused("pipeline_over_budget")
    _,models,peak=winner
    pinned=tuple(sorted(pins.items()))
    payload={"context":[context.task,context.session,context.profile,context.generation,
                       context.route,context.mode,context.offline,context.public_opt_in],
             "models":[[c.identity,c.task,c.locality,c.provider,c.compatible,c.healthy,
                        c.memory_mib,c.rank] for c in models],
             "strategy":strategy,"residency":residency,"budget":budget_mib,
             "reserve":reserve_mib,"peak":peak,"pinned":pinned}
    digest=sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode("ascii")).hexdigest()
    return VoicePlan(context,models,strategy,residency,budget_mib,reserve_mib,peak,pinned,digest)

def revalidate(expected,context,candidates):
    """Recompute from current trusted inventory metadata; never auto-switch."""
    if type(expected) is not VoicePlan:
        raise Refused("invalid_pipeline_plan")
    current=plan_voice(context,candidates,expected.budget_mib,
        reserve_mib=expected.reserve_mib,strategy=expected.strategy,
        residency=expected.residency,pinned=expected.pinned)
    if current!=expected:
        raise Refused("pipeline_plan_changed")
    return current
