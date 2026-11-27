# SPDX-License-Identifier: AGPL-3.0-only
"""Closed public tiny-model fixture wire. No provider or process access."""
from hashlib import sha256
import json
from gateway import Refused
from local_agent_wave_run import AgentResult
from llama_engine_acceptance import parse_reply,MODEL,MODEL_SHA

PARENTS={"plan":(),"a":("plan",),"b":("plan",),"review":("a","b")}
PREFIX={"plan":"Once upon a time, there was a little bird named",
        "a":"The little bird went to the garden. ",
        "b":"The little bird met a friend. ",
        "review":"The little bird and its friend went home. "}
MAX_WIRE=4096
MAX_PARENT_BYTES=512

def wire_for(worker,rows):
    if type(worker) is not str or worker not in PARENTS or type(rows) is not tuple or len(rows)!=len(PARENTS[worker]):
        raise Refused("model_fixture_inputs")
    texts=[]
    for parent,row in zip(PARENTS[worker],rows):
        if type(row) is not tuple or len(row)!=2:
            raise Refused("model_fixture_inputs")
        receipt,data=row
        if (type(receipt) is not AgentResult or receipt.worker!=parent or type(data) is not bytes
                or not 0<len(data)<=MAX_PARENT_BYTES or receipt.size!=len(data)
                or receipt.payload_digest!=sha256(data).hexdigest()
                or type(receipt.input_digest) is not str or len(receipt.input_digest)!=64):
            raise Refused("model_fixture_receipt")
        try:
            value=data.decode("utf-8")
        except UnicodeError as exc:
            raise Refused("model_fixture_text") from exc
        if not value.strip() or any(ord(c)<32 and c not in "\n\t" or 127<=ord(c)<=159 for c in value):
            raise Refused("model_fixture_text")
        texts.append(value)
    prompt=PREFIX[worker]+(" ".join(texts)+" " if texts else "")
    wire=json.dumps({"prompt":prompt,"n_predict":16,"temperature":0,"seed":42,
                     "stream":False,"return_tokens":True,"cache_prompt":False,
                     "response_fields":["content","tokens","stop","stop_type","model","truncated"]},
                    separators=(",",":"),ensure_ascii=True).encode("ascii")
    if len(wire)>MAX_WIRE:
        raise Refused("model_fixture_wire_size")
    validate_wire(worker,wire)
    return wire

def validate_wire(worker,wire):
    """Closed canonical request for one exact fixed worker, never user input."""
    if type(worker) is not str or worker not in PREFIX or type(wire) is not bytes or not 0<len(wire)<=MAX_WIRE:
        raise Refused("model_fixture_wire")
    try:
        obj=json.loads(wire)
    except (ValueError,UnicodeError) as exc:
        raise Refused("model_fixture_wire") from exc
    if type(obj) is not dict or type(obj.get("prompt")) is not str:
        raise Refused("model_fixture_wire")
    prompt=obj["prompt"]
    expected={"prompt":prompt,"n_predict":16,"temperature":0,"seed":42,
              "stream":False,"return_tokens":True,"cache_prompt":False,
              "response_fields":["content","tokens","stop","stop_type","model","truncated"]}
    if (not prompt.startswith(PREFIX[worker]) or len(prompt.encode("utf-8"))>2048
            or any(ord(x)<32 and x not in "\\n\\t" or 127<=ord(x)<=159 for x in prompt)
            or (worker=="plan" and prompt!=PREFIX[worker])
            or (worker!="plan" and not prompt[len(PREFIX[worker]):].strip())
            or wire!=json.dumps(expected,separators=(",",":"),ensure_ascii=True).encode("ascii")):
        raise Refused("model_fixture_wire")
    return sha256(wire).hexdigest()

def reply_for(raw):
    content,tokens=parse_reply(raw)
    data=content.encode("utf-8")
    if not 0<len(data)<=MAX_PARENT_BYTES:
        raise Refused("model_fixture_result_size")
    return data,tokens

def bound_reply(worker,wire,envelope):
    wire_digest=validate_wire(worker,wire)
    if type(envelope) is not bytes or not 0<len(envelope)<=65536:
        raise Refused("model_fixture_envelope")
    try:
        obj=json.loads(envelope)
    except (ValueError,UnicodeError) as exc:
        raise Refused("model_fixture_envelope") from exc
    if (type(obj) is not dict or set(obj)!={"worker","wire_digest","model_digest","reply","server_reaped"}
            or obj["worker"]!=worker or obj["wire_digest"]!=wire_digest
            or obj["model_digest"]!=MODEL_SHA or obj["server_reaped"] is not True
            or type(obj["reply"]) is not str
            or envelope!=json.dumps(obj,separators=(",",":"),ensure_ascii=True).encode("ascii")):
        raise Refused("model_fixture_envelope")
    return reply_for(obj["reply"].encode("utf-8"))
