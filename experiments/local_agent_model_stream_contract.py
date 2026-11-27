# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed public native stream cancellation contract, no transport or host access."""
import json
from hashlib import sha256
from gateway import Refused
from local_agent_model_fixture_contract import validate_wire,_unique,wire_ceiling

MAX_EVENT=8192

def stream_wire(worker,wire):
    validate_wire(worker,wire)
    obj=json.loads(wire)
    obj["stream"]=True
    obj["response_fields"]=["content","tokens","stop"]
    return json.dumps(obj,separators=(",",":"),ensure_ascii=True).encode("ascii")

def first_event(line):
    if type(line) is not bytes or not 0<len(line)<=MAX_EVENT or not line.startswith(b"data: ") or not line.endswith(b"\n"):
        raise Refused("fixture_stream_frame")
    try:
        obj=json.loads(line[6:].decode("utf-8"),object_pairs_hook=_unique)
    except (ValueError,UnicodeError) as exc:
        raise Refused("fixture_stream_json") from exc
    if (type(obj) is not dict or not {"content","tokens","stop"}<=set(obj)
            or obj["stop"] is not False or type(obj["content"]) is not str
            or type(obj["tokens"]) is not list or not 1<=len(obj["tokens"])<=16
            or any(type(x) is not int or not 0<=x<512 for x in obj["tokens"])):
        raise Refused("fixture_stream_event")
    try:
        payload=obj["content"].encode("utf-8")
    except UnicodeError as exc:
        raise Refused("fixture_stream_text") from exc
    if len(payload)>512 or any(ord(x)<32 and x not in "\n\t" or 127<=ord(x)<=159 for x in obj["content"]):
        raise Refused("fixture_stream_text")
    # No text or token IDs leave this contract, only bounded evidence.
    return len(obj["tokens"]),sha256(line).hexdigest()

def marker(worker,wire,line):
    count,event_digest=first_event(line)
    if count>wire_ceiling(worker,wire):
        raise Refused("fixture_stream_event_ceiling")
    return {"state":"FIRST_EVENT","worker":worker,
            "wire_digest":sha256(stream_wire(worker,wire)).hexdigest(),
            "event_digest":event_digest,"observed_tokens":count}

def verify_marker(worker,wire,obj):
    if (type(obj) is not dict or set(obj)!={"state","worker","wire_digest","event_digest","observed_tokens"}
            or obj["state"]!="FIRST_EVENT" or obj["worker"]!=worker
            or obj["wire_digest"]!=sha256(stream_wire(worker,wire)).hexdigest()
            or type(obj["observed_tokens"]) is not int or not 1<=obj["observed_tokens"]<=wire_ceiling(worker,wire)
            or type(obj["event_digest"]) is not str or len(obj["event_digest"])!=64
            or any(x not in "0123456789abcdef" for x in obj["event_digest"])):
        raise Refused("fixture_stream_marker")
    return obj["observed_tokens"]

def verify_cancel(worker,wire,obj):
    if (type(obj) is not dict or set(obj)!={"state","worker","wire_digest","server_reaped"}
            or obj["state"]!="CANCELLED" or obj["worker"]!=worker
            or obj["wire_digest"]!=sha256(stream_wire(worker,wire)).hexdigest()
            or obj["server_reaped"] is not True):
        raise Refused("fixture_stream_cancel")
