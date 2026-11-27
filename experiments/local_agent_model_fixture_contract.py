# SPDX-License-Identifier: AGPL-3.0-only
"""Closed public tiny-model fixture wire. No provider or process access."""
from hashlib import sha256
import json
from gateway import Refused
from local_agent_wave_run import AgentResult
from llama_engine_acceptance import parse_reply,MODEL

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
    return wire

def reply_for(raw):
    content,tokens=parse_reply(raw)
    data=content.encode("utf-8")
    if not 0<len(data)<=MAX_PARENT_BYTES:
        raise Refused("model_fixture_result_size")
    return data,tokens
