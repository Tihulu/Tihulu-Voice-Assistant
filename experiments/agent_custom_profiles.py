# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping custom agent definitions. Profiles describe preferences, never grants."""
from dataclasses import dataclass
from hashlib import sha256
import json
from gateway import Refused,token
from heterogeneous_agents import AgentSpec,ROLES,ORDER

def ident(value):
    if type(value) is not str:
        raise Refused("agent_profile_identity")
    return token(value)

@dataclass(frozen=True)
class AgentProfile:
    identity: str
    revision: int
    role: str
    locality: str
    channels: tuple
    preference: str
    model: str
    provider: str
    memory_mib: int
    output_tokens: int

    def checked(self):
        ident(self.identity)
        if (type(self.revision) is not int or not 1<=self.revision<=1000000
                or type(self.role) is not str or self.role not in ROLES
                or type(self.locality) is not str or self.locality not in ("local","cloud")
                or type(self.channels) is not tuple or not 1<=len(self.channels)<=len(ORDER)
                or any(type(c) is not str or c not in ROLES[self.role] for c in self.channels)
                or len(set(self.channels))!=len(self.channels)
                or type(self.preference) is not str or self.preference not in ("economy","balanced","performance")
                or type(self.memory_mib) is not int or not 0<=self.memory_mib<=1048576
                or type(self.output_tokens) is not int or not 1<=self.output_tokens<=65536):
            raise Refused("agent_profile_invalid")
        if self.locality=="cloud" and (self.channels!=("reasoning",) or self.memory_mib!=0):
            raise Refused("agent_profile_cloud_scope")
        for field in (self.model,self.provider):
            if type(field) is not str:
                raise Refused("agent_profile_model")
            if field:
                ident(field)
        return self

    def digest(self):
        self.checked()
        return sha256(json.dumps(("agent-profile-v1",self.identity,self.revision,self.role,self.locality,
            tuple(sorted(self.channels)),self.preference,self.model,self.provider,self.memory_mib,
            self.output_tokens),separators=(",",":")).encode("ascii")).hexdigest()

    def spec(self,agent,alternatives):
        self.checked()
        ident(agent)
        if (type(alternatives) is not tuple or not alternatives
                or any(type(c) is not str or c not in self.channels for c in alternatives)
                or len(set(alternatives))!=len(alternatives)):
            raise Refused("agent_profile_task_scope")
        return AgentSpec(agent,self.role,self.locality,self.channels,alternatives)

def import_profile(raw):
    """Strict data-only manifest; does not restore permissions or auto-approve."""
    if type(raw) is not bytes or not 1<=len(raw)<=8192:
        raise Refused("agent_profile_size")
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:
                raise Refused("agent_profile_duplicate")
            obj[k]=v
        return obj
    try:
        obj=json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise Refused("agent_profile_json") from exc
    if type(obj) is not dict or set(obj)!=set(AgentProfile.__dataclass_fields__) or type(obj["channels"]) is not list:
        raise Refused("agent_profile_schema")
    obj["channels"]=tuple(obj["channels"])
    return AgentProfile(**obj).checked()
