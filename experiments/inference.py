# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic inference choice and minimum-field disclosure. No real model or provider."""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from gateway import Request, Refused, token

@dataclass(frozen=True)
class Candidate:
    identity: str
    task: str
    locality: str
    provider: str
    compatible: bool
    healthy: bool
    memory_mib: int
    rank: int

    def checked(self):
        for x in (self.identity, self.provider):
            token(x)
        if (self.task not in ("stt", "llm", "tts")
                or self.locality not in ("local", "cloud")
                or type(self.compatible) is not bool or type(self.healthy) is not bool
                or type(self.memory_mib) is not int or not 0 <= self.memory_mib <= 1048576
                or type(self.rank) is not int or not 0 <= self.rank <= 100):
            raise Refused("invalid_candidate")
        return self

def choose(context, candidates, task, budget, preference, approved_cloud=(), *, purpose="primary"):
    """Plan only. Never silently escalate from local to cloud."""
    context.checked()
    if (task not in ("stt", "llm", "tts") or type(candidates) is not tuple
            or len(candidates) > 64 or type(budget) is not int or not 0 <= budget <= 1048576
            or preference not in ("local", "cloud") or purpose not in ("primary", "consultation")
            or type(approved_cloud) is not tuple or len(approved_cloud) > 32):
        raise Refused("invalid_selection")
    for p in approved_cloud:
        token(p)
    if preference == "cloud" and (context.mode == "LOCAL_ONLY" or context.offline):
        raise Refused("cloud_disabled")
    if context.mode == "CLOUD_CHAT" and preference == "local":
        raise Refused("local_reasoning_not_selected")
    # Secure Hybrid's main planner and executor are always local. A cloud
    # choice is only a separately approved minimized consultation candidate,
    # never an alternative primary route or a remote voice engine.
    if purpose == "consultation":
        if context.mode != "SECURE_HYBRID" or preference != "cloud" or task != "llm":
            raise Refused("invalid_consultation_route")
    elif context.mode == "SECURE_HYBRID" and preference == "cloud":
        raise Refused("secure_primary_must_be_local")
    seen = set()
    eligible = []
    for candidate in candidates:
        if type(candidate) is not Candidate:
            raise Refused("invalid_candidate")
        candidate.checked()
        if candidate.identity in seen:
            raise Refused("duplicate_candidate")
        seen.add(candidate.identity)
        if (candidate.task == task and candidate.locality == preference and candidate.compatible
                and candidate.healthy and candidate.memory_mib <= budget
                and (preference == "local" or candidate.provider in approved_cloud)):
            eligible.append(candidate)
    if not eligible:
        raise Refused("no_eligible_route")
    return min(eligible, key=lambda c: (-c.rank, c.memory_mib, c.identity))

# Closed schema: no free-text, filenames, terminal output, credentials or raw records.
FIELDS = {"count": (0, 1000000), "min": (-1000000, 1000000), "max": (-1000000, 1000000)}
@dataclass(frozen=True)
class Envelope:
    destination: str
    payload: bytes
    digest: str

def envelope(gateway, grant, context, destination, values, fields):
    token(destination)
    if context.mode != "SECURE_HYBRID":
        raise Refused("secure_hybrid_required")
    req = Request(context, "disclose", destination, "local", "minimized")
    gateway.checkpoint(grant, req)
    if (type(values) is not dict or len(values) > 16 or type(fields) is not tuple
            or not 1 <= len(fields) <= len(FIELDS) or any(type(k) is not str or k not in FIELDS for k in fields)
            or len(set(fields)) != len(fields)):
        raise Refused("invalid_disclosure_schema")
    selected = {}
    for k in fields:
        v = values.get(k)
        lo, hi = FIELDS[k]
        if type(v) is not int or not lo <= v <= hi:
            raise Refused("invalid_disclosure_value")
        selected[k] = v
    if "min" in selected and "max" in selected and selected["min"] > selected["max"]:
        raise Refused("invalid_range")
    data = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("ascii")
    gateway.checkpoint(grant, req)
    return Envelope(destination, data, sha256(data).hexdigest())
