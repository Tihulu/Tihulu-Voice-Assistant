# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping model adapter request/response and exact disclosure consent.

This is an internal normalized fixture protocol, not a claim of Jan or
provider wire compatibility. No client, network or inference is invoked.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import Lock
from gateway import Grant, Request, Refused, token
import re
from inference import Candidate, Envelope

MAX_TEXT=16384
MAX_FRAME=32768

def text_bytes(value):
    if type(value) is not str or not value or len(value)>MAX_TEXT:
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

def local_request(context, call, *, dependent=False):
    # Dependency-bound document data stays private and needs the additional
    # host ceiling. The old standalone synthetic adapter request is unchanged.
    if dependent:
        return Request(context,"document_engine",call.target,"local","private").checked()
    return Request(context,"engine",call.target,"local").checked()


# Fixed disposable native completion profile, not an arbitrary endpoint.
VOICE_NATIVE_MODEL="stories260k-047bf46455a544931cff6fef14d7910154c56afbc23ab1c5e56a72e69912c04b"
VOICE_NATIVE_PROVIDER="llama-4762ad7316dcdec20016ab5985fb46a27902204d"

def native_voice_payload(text):
    if len(text_bytes(text))>1024:
        raise Refused("native_voice_prompt_limit")
    return json.dumps({"prompt":text,"n_predict":16,"temperature":0,"seed":42,
        "stream":False,"return_tokens":True,"cache_prompt":False,
        "response_fields":["content","tokens","stop","stop_type","model","truncated"]},
        ensure_ascii=False,separators=(",",":")).encode("utf-8")

class LocalAdapter:
    """Owner-side state machine, not a backend. All payloads synthetic."""
    def __init__(self,gateway,context,candidate,identity,operation,data,grant,*,dependencies=(),response_language=None,review_grant=None):
        if type(candidate) is not Candidate or candidate.locality!="local":
            raise Refused("local_candidate_required")
        self._call=prepare(candidate,identity,operation,data)
        self._wire_digest=self._call.input_digest
        self._native_voice=False
        voice_receipt=None
        # A reserved voice envelope cannot silently downgrade to a generic
        # invocation just by omitting the optional language parameter.
        try:
            obj=json.loads(data)
        except (ValueError,UnicodeError,RecursionError):
            obj=None
        voice=type(obj) is dict and obj.get("kind")=="voice_turn"
        if response_language is not None or voice:
            if (not voice or type(response_language) is not str or response_language not in ("tr","en","de")
                    or operation!="llm" or type(dependencies) is not tuple or len(dependencies)!=1):
                raise Refused("invalid_bound_language")
            required={"kind","artifact","source_digest","model_digest","transcript_digest",
                      "input_language","response_language","transcript","transcript_grant","transcript_target",
                      "submitted_digest","edited"}
            if set(obj) not in (required,required|{"native_wire_digest"}):
                raise Refused("invalid_voice_envelope")
            if json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")!=data:
                raise Refused("invalid_voice_envelope")
            if (obj["response_language"]!=response_language or obj["input_language"] not in ("tr","en","de")
                    or sha256(text_bytes(obj["transcript"])).hexdigest()!=obj["submitted_digest"]
                    or type(obj["edited"]) is not bool
                    or obj["edited"]!=(obj["submitted_digest"]!=obj["transcript_digest"])
                    or not obj["transcript"].strip()):
                raise Refused("voice_language_or_text_mismatch")
            for key in ("source_digest","model_digest","transcript_digest","transcript_target","submitted_digest"):
                if type(obj[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}",obj[key]):
                    raise Refused("invalid_voice_digest")
            for key in ("artifact","transcript_grant"):
                if type(obj[key]) is not str:
                    raise Refused("invalid_voice_identity")
                token(obj[key])
            parent=dependencies[0]
            if type(parent) is not tuple or len(parent)!=2:
                raise Refused("invalid_voice_parent")
            parent_grant,parent_req=parent
            if (type(parent_grant) is not Grant or type(parent_req) is not Request
                    or parent_grant.identity!=obj["transcript_grant"] or parent_req.target!=obj["transcript_target"]
                    or parent_grant.request!=parent_req or parent_req.context!=context
                    or parent_req.action!="application_engine" or parent_req.actor!="local"
                    or parent_req.classification!="private"):
                raise Refused("invalid_voice_parent")
            voice_receipt=(obj["artifact"],obj["source_digest"],obj["model_digest"],
                           obj["input_language"],obj["transcript_digest"])
            if "native_wire_digest" in obj:
                if (response_language!="en" or obj["input_language"]!="en"
                        or candidate.identity!=VOICE_NATIVE_MODEL or candidate.provider!=VOICE_NATIVE_PROVIDER):
                    raise Refused("native_voice_profile_denied")
                digest=sha256(native_voice_payload(obj["transcript"])).hexdigest()
                if obj["native_wire_digest"]!=digest:
                    raise Refused("native_voice_wire_mismatch")
                self._wire_digest=digest
                self._native_voice=True
        self._response_language=response_language
        self._g,self._grant=gateway,grant
        if type(dependencies) is not tuple:
            raise Refused("invalid_dependency_group")
        self._dependencies=dependencies
        self._req=local_request(context,self.call,dependent=bool(dependencies))
        if voice_receipt is not None:
            self._g.claim_voice_turn(grant,self._req,dependencies,voice_receipt,review_grant)
        elif review_grant is not None:
            raise Refused("unexpected_voice_review")
        elif dependencies:
            self._g.claim_with_dependencies(grant,self._req,dependencies)
        else:
            self._g.claim(grant,self._req)
        self._state="ready"
        self._transport_started=False
        self._stream_owner=None
        self._seq=0
        self._result=bytearray()
        self._lock=Lock()
    def _checkpoint(self):
        if self._dependencies:
            self._g.checkpoint_claimed_group(self._dependencies+((self._grant,self._req),))
        else:
            self._g.checkpoint(self._grant,self._req)

    def begin_fixture_transport(self,data):
        """One exact bounded private fixture request; no actual network grant."""
        with self._lock:
            if self._state!="ready" or self._transport_started:
                raise Refused("transport_already_started")
            self._checkpoint()
            if type(data) is not bytes or not 0<len(data)<=MAX_TEXT or sha256(data).hexdigest()!=self._wire_digest:
                raise Refused("transport_input_mismatch")
            self._transport_started=True

    def checkpoint_fixture_transport(self):
        with self._lock:
            if not self._transport_started or self._state not in ("ready","running"):
                raise Refused("transport_not_live")
            self._checkpoint()

    @property
    def call(self):
        return self._call

    @property
    def state(self):
        with self._lock:
            return self._state
    def attach_fixture_stream(self,owner):
        with self._lock:
            if (type(owner) is not object or self._state!="ready" or self._seq!=0
                    or self._stream_owner is not None or self._native_voice):
                raise Refused("stream_owner_unavailable")
            self._checkpoint()
            self._stream_owner=owner

    def checkpoint_fixture_stream(self,owner):
        with self._lock:
            if self._stream_owner is not owner or owner is None or self._state not in ("ready","running"):
                raise Refused("stream_not_live")
            self._checkpoint()

    def stream_event(self,owner,identity,sequence,kind,value):
        with self._lock:
            if self._stream_owner is not owner or owner is None:
                raise Refused("stream_owner_mismatch")
            return self._event(identity,sequence,kind,value)

    def abort_fixture_stream(self,owner):
        with self._lock:
            if self._stream_owner is not owner or owner is None:
                raise Refused("stream_owner_mismatch")
            if self._state!="completed":
                self._result.clear()
                self._state="cancelled"
                self._g.revoke(self._grant.identity)

    def event(self,identity,sequence,kind,value):
        with self._lock:
            if self._stream_owner is not None:
                raise Refused("stream_owner_mismatch")
            return self._event(identity,sequence,kind,value)
    def frame(self, raw):
        """Strict offline normalized frame, not Jan/OpenAI wire compatibility.

        The exact invocation grant prevents late events from a previous call
        with the same model/request identity entering a new call.
        """
        with self._lock:
            if self._stream_owner is not None:
                raise Refused("stream_owner_mismatch")
            if self._state not in ("ready","running"):
                raise Refused("terminal_call")
            try:
                self._checkpoint()
                if type(raw) is not bytes or not 0<len(raw)<=MAX_FRAME:
                    raise Refused("invalid_stream_frame")
                def unique(pairs):
                    obj={}
                    for key,value in pairs:
                        if key in obj:
                            raise Refused("duplicate_stream_field")
                        obj[key]=value
                    return obj
                try:
                    obj=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
                except (ValueError,UnicodeError,RecursionError) as exc:
                    raise Refused("invalid_stream_frame") from exc
                if (type(obj) is not dict or set(obj)!={"grant","target","identity","sequence","kind","value"}
                        or type(obj["grant"]) is not str or obj["grant"]!=self._grant.identity
                        or type(obj["target"]) is not str or obj["target"]!=self._req.target
                        or type(obj["identity"]) is not str or obj["identity"]!=self.call.identity
                        or type(obj["kind"]) is not str):
                    raise Refused("stream_invocation_mismatch")
                return self._event(obj["identity"],obj["sequence"],obj["kind"],obj["value"])
            except Refused:
                self._result.clear()
                self._state="held"
                raise

    def _event(self,identity,sequence,kind,value):
        if self._state not in ("ready","running"):
            raise Refused("terminal_call")
        try:
            self._checkpoint()
            if self._native_voice and not self._transport_started:
                raise Refused("native_voice_transport_required")
            if (type(identity) is not str or identity!=self.call.identity or type(sequence) is not int
                    or sequence!=self._seq+1 or sequence>256
                    or type(kind) is not str or kind not in ("text","completed","failed")):
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
            self._checkpoint()
            self._seq=sequence
        except Refused:
            self._result.clear()
            self._state="held"
            raise
    def result(self):
        with self._lock:
            return self._completed_result()
    def _completed_result(self):
        if self._state!="completed":
            raise Refused("no_completed_result")
        try:
            self._checkpoint()
        except Refused:
            self._result.clear()
            self._state="held"
            raise
        return bytes(self._result)
    def _speech_call(self,version,language,artifact,output):
        from capabilities import CapabilityCall
        if self.call.operation!="llm":
            raise Refused("local_llm_required")
        if self._response_language is not None and language!=self._response_language:
            raise Refused("voice_language_mismatch")
        payload=self._completed_result()
        call=CapabilityCall("espeak_ng",version,"text_to_wav",
                    (("artifact",artifact),("digest",sha256(payload).hexdigest()),
                     ("language",language),("output",output))).checked()
        return call,payload

    def prepare_speech(self,version,language,artifact,output):
        with self._lock:
            call,_=self._speech_call(version,language,artifact,output)
            return call

    def start_speech(self,version,language,artifact,output,grant):
        from speech_result import SpeechFixture
        with self._lock:
            call,payload=self._speech_call(version,language,artifact,output)
            job=SpeechFixture(self._g,self._req.context,call,grant,
                              dependencies=((self._grant,self._req),))
            return job,payload

    def cancel(self):
        with self._lock:
            self._result.clear()
            self._state="cancelled"
            self._g.revoke(self._grant.identity)

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
    gateway.claim(grant,req)
    gateway.checkpoint(grant,req)
    return envelope.payload
