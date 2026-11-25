# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping model adapter request/response and exact disclosure consent.

This is an internal normalized fixture protocol, not a claim of Jan or
provider wire compatibility. No client, network or inference is invoked.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from gateway import Request, Refused, token
from inference import Candidate, Envelope

MAX_TEXT=16384

def text_bytes(value):
    if not isinstance(value,str) or not value or len(value)>MAX_TEXT:
        raise Refused("invalid_text")
    if any((ord(c)<32 and c not in "\n\t") or 127<=ord(c)<=159
           or 0xd800<=ord(c)<=0xdfff for c in value):
        raise Refused("invalid_text")
    data=value.encode("utf-8")
    if len(data)>MAX_TEXT:
        raise Refused("invalid_text")
    return data

@dataclass(frozen=True)
class Call:
    identity: str
    model: str
    provider: str
    operation: str
    input_digest: str
    target: str

def prepare(candidate, identity, operation, data):
    if type(candidate) is not Candidate:
        raise Refused("invalid_candidate")
    candidate.checked()
    token(identity)
    if operation!=candidate.task or not candidate.compatible or not candidate.healthy:
        raise Refused("candidate_unavailable")
    if type(data) is not bytes or not 0<len(data)<=MAX_TEXT:
        raise Refused("invalid_input")
    digest=sha256(data).hexdigest()
    target=sha256(repr((identity,candidate.identity,candidate.provider,
                       candidate.locality,operation,digest)).encode("ascii")).hexdigest()
    return Call(identity,candidate.identity,candidate.provider,operation,digest,target)

class LocalAdapter:
    """Owner-side state machine, not a backend. All payloads synthetic."""
    def __init__(self,gateway,context,candidate,identity,operation,data,grant):
        if type(candidate) is not Candidate or candidate.locality!="local":
            raise Refused("local_candidate_required")
        self.call=prepare(candidate,identity,operation,data)
        self._g,self._grant=gateway,grant
        self._req=Request(context,"engine",self.call.target,"local")
        self._g.claim(grant,self._req)
        self._state="ready"
        self._seq=0
        self._result=bytearray()
    @property
    def state(self):
        return self._state
    def event(self,identity,sequence,kind,value):
        if self._state not in ("ready","running"):
            raise Refused("terminal_call")
        try:
            self._g.checkpoint(self._grant,self._req)
            if (identity!=self.call.identity or type(sequence) is not int
                    or sequence!=self._seq+1 or sequence>256
                    or kind not in ("text","completed","failed")):
                raise Refused("invalid_event")
            if kind=="text":
                if self.call.operation=="tts":
                    raise Refused("unsupported_fixture_output")
                data=text_bytes(value)
                if len(self._result)+len(data)>MAX_TEXT:
                    raise Refused("output_limit")
                self._result.extend(data)
                self._state="running"
            elif value is not None:
                raise Refused("invalid_terminal_payload")
            elif kind=="completed":
                if not self._result:
                    raise Refused("empty_output")
                self._state="completed"
            else:
                self._result.clear()
                self._state="failed"
            self._g.checkpoint(self._grant,self._req)
            self._seq=sequence
        except Refused:
            self._result.clear()
            self._state="held"
            raise
    def result(self):
        if self._state!="completed":
            raise Refused("no_completed_result")
        try:
            self._g.checkpoint(self._grant,self._req)
        except Refused:
            self._result.clear()
            self._state="held"
            raise
        return bytes(self._result)
    def cancel(self):
        self._result.clear()
        self._state="cancelled"

def disclosure_target(envelope):
    if type(envelope) is not Envelope:
        raise Refused("invalid_envelope")
    token(envelope.destination)
    if type(envelope.payload) is not bytes or not 0<len(envelope.payload)<=128:
        raise Refused("invalid_envelope")
    if sha256(envelope.payload).hexdigest()!=envelope.digest:
        raise Refused("invalid_envelope")
    try:
        obj=json.loads(envelope.payload)
    except (ValueError,UnicodeError):
        raise Refused("invalid_envelope") from None
    from inference import FIELDS
    if (type(obj) is not dict or not obj or len(obj)>len(FIELDS)
            or any(k not in FIELDS or type(v) is not int
                   or not FIELDS[k][0]<=v<=FIELDS[k][1] for k,v in obj.items())
            or ("min" in obj and "max" in obj and obj["min"]>obj["max"])
            or json.dumps(obj,sort_keys=True,separators=(",",":")).encode("ascii")!=envelope.payload):
        raise Refused("invalid_envelope")
    return sha256(repr((envelope.destination,envelope.digest)).encode("ascii")).hexdigest()

def approved_payload(gateway,grant,context,envelope):
    """Fixture-only exact payload/destination check; no send method exists."""
    target=disclosure_target(envelope)
    if context.mode!="SECURE_HYBRID":
        raise Refused("secure_hybrid_required")
    req=Request(context,"disclose",target,"local","minimized")
    gateway.checkpoint(grant,req)
    return envelope.payload
