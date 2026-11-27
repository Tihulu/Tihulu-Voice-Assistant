# SPDX-License-Identifier: AGPL-3.0-only
"""Heterogeneous agent planning catalog. No executor, permissions or I/O."""
from dataclasses import dataclass
from types import MappingProxyType
from gateway import Context,Refused,token

# Role describes purpose; channel describes the proposed interface, not a grant.
ROLES=MappingProxyType({
    "orchestrator":("reasoning",),
    "reasoning":("reasoning",),
    "planner":("reasoning",),
    "reviewer":("reasoning","file_read"),
    "research":("public_search","browser_read","connector_read","offline_browser_read"),
    "coding":("file_read","file_write","code_session","local_cli"),
    "terminal":("local_cli",),
    "application":("typed_engine","local_ipc","local_cli"),
    "file":("file_read","file_write"),
    "data_science":("typed_engine","file_read","local_cli"),
    "database":("connector_read","connector_write","typed_engine"),
    "browser":("browser_read","browser_action","offline_browser_read","offline_browser_action"),
    "vision":("image_review","screen_observe"),
    "desktop":("screen_observe","accessibility_read","desktop_input"),
    "accessibility":("accessibility_read","desktop_input"),
    "media":("typed_engine","file_read","file_write"),
    "voice":("voice_input","voice_output"),
    "document":("file_read","typed_engine","image_review","file_write"),
    "security_qa":("reasoning","file_read","typed_engine","local_cli","screen_observe"),
    "creative":("reasoning","typed_engine","image_review"),
    "connector":("connector_read","connector_write"),
    "workflow":("reasoning","schedule"),
})
# Planning-only cloud advice is never a host tool channel.
HOST=frozenset(c for channels in ROLES.values() for c in channels)-{"reasoning","public_search"}
SCREEN=frozenset(("screen_observe","desktop_input"))
NETWORK=frozenset(("public_search","browser_read","browser_action","connector_read","connector_write"))
PUBLIC_NETWORK=frozenset(("public_search","browser_read"))
EFFECTS=frozenset(("file_write","code_session","local_cli","local_ipc","typed_engine",
                   "connector_write","browser_action","offline_browser_action","desktop_input","voice_input","voice_output","schedule"))
ORDER=("reasoning","file_read","typed_engine","local_ipc","local_cli","code_session",
       "connector_read","connector_write","offline_browser_read","offline_browser_action","public_search","browser_read","browser_action",
       "image_review","accessibility_read","screen_observe","desktop_input","file_write",
       "voice_input","voice_output","schedule")
@dataclass(frozen=True)
class AgentSpec:
    identity: str
    role: str
    locality: str
    channels: tuple
    needs: tuple

@dataclass(frozen=True)
class Route:
    agent: str
    role: str
    locality: str
    channel: str
    requires_screen: bool
    effect_review: bool

def plan_route(context,spec,available):
    if type(context) is not Context or type(spec) is not AgentSpec:
        raise Refused("agent_catalog_input")
    context.checked()
    if type(spec.identity) is not str:
        raise Refused("agent_catalog_identity")
    token(spec.identity)
    if (type(spec.role) is not str or spec.role not in ROLES or type(spec.locality) is not str
            or spec.locality not in ("local","cloud") or type(spec.channels) is not tuple
            or type(spec.needs) is not tuple or type(available) is not tuple):
        raise Refused("agent_catalog_input")
    for group in (spec.channels,spec.needs,available):
        if not 1<=len(group)<=len(ORDER) or any(type(c) is not str or c not in ORDER for c in group) or len(set(group))!=len(group):
            raise Refused("agent_catalog_channels")
    if not set(spec.channels)<=set(ROLES[spec.role]) or not set(spec.needs)<=set(spec.channels):
        raise Refused("agent_catalog_role_scope")
    candidates=set(spec.needs)&set(available)
    if spec.locality=="cloud":
        candidates&={"reasoning"}
        if context.offline or context.mode=="LOCAL_ONLY":
            candidates.clear()
    elif context.mode=="CLOUD_CHAT":
        candidates&={"reasoning"}
    if context.offline:
        candidates-=NETWORK
    elif context.mode=="LOCAL_ONLY":
        candidates-=NETWORK-(PUBLIC_NETWORK if context.public_opt_in else set())
    if not candidates:
        raise Refused("agent_catalog_hold")
    channel=next(c for c in ORDER if c in candidates)
    return Route(spec.identity,spec.role,spec.locality,channel,channel in SCREEN,channel in EFFECTS)
