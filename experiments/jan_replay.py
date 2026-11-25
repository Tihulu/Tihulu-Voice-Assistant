# SPDX-License-Identifier: AGPL-3.0-only
"""Offline replay of existing Jan HTTP/SSE framing through owned adapter.

No socket is opened. Reuses the shipped Jan parser; the additional strict
schema deliberately excludes tool calls, reasoning fields and multiple choices.
Actual version/server compatibility still needs a separately accepted live test.
"""
import asyncio
import json
from dataclasses import dataclass
from gateway import Refused
from adapter_protocol import LocalAdapter, prepare, text_bytes
from inference import Candidate
from tihulu_jarvis.voice import jan
from tihulu_jarvis.voice.m1 import Hold

@dataclass(frozen=True)
class Turn:
    role: str
    text: str

def request_bytes(candidate,url,turns):
    if type(candidate) is not Candidate or candidate.provider!="jan" or candidate.locality!="local" or candidate.task!="llm":
        raise Refused("jan_candidate_required")
    candidate.checked()
    try:
        ip,port=jan.endpoint(url)
    except Hold:
        raise Refused("invalid_loopback_endpoint") from None
    if type(turns) is not tuple or not 1<=len(turns)<=32:
        raise Refused("invalid_turns")
    messages=[]
    for turn in turns:
        if type(turn) is not Turn or turn.role not in ("system","user","assistant"):
            raise Refused("invalid_turn")
        text_bytes(turn.text)
        messages.append({"role":turn.role,"content":turn.text})
    if turns[-1].role!="user":
        raise Refused("user_turn_required")
    # Endpoint + request bytes are included in the exact local operation grant.
    body={"endpoint":str(ip)+":"+str(port),"body":{"model":candidate.identity,
            "stream":True,"messages":messages}}
    raw=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
    if len(raw)>16384:
        raise Refused("request_limit")
    return raw

def unique_pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out:
            raise Refused("duplicate_json_key")
        out[k]=v
    return out

async def checked_lines(lines,model):
    async for field in lines:
        if field and not field.startswith(b":"):
            if not field.startswith(b"data:"):
                raise Refused("unsupported_event")
            data=field[5:].strip()
            if data!=b"[DONE]":
                try:
                    obj=json.loads(data.decode("utf-8"),object_pairs_hook=unique_pairs)
                    if (type(obj) is not dict or "error" in obj
                            or ("model" in obj and obj["model"]!=model)):
                        raise Refused("invalid_model_event")
                    choices=obj.get("choices")
                    if type(choices) is not list or len(choices)!=1:
                        raise Refused("invalid_choices")
                    c=choices[0]
                    if (type(c) is not dict or set(c)-{"index","delta","finish_reason"}
                            or ("index" in c and (type(c["index"]) is not int or c["index"]!=0))
                            or c.get("finish_reason") not in (None,"stop","length")):
                        raise Refused("unsupported_choice")
                    delta=c.get("delta")
                    if (type(delta) is not dict or set(delta)-{"role","content"}
                            or ("role" in delta and delta["role"]!="assistant")):
                        raise Refused("unsupported_delta")
                    if delta.get("content") is not None and delta["content"]!="":
                        text_bytes(delta["content"])
                except (ValueError,UnicodeError,RecursionError) as e:
                    if isinstance(e,Refused):
                        raise
                    raise Refused("invalid_model_event") from None
        yield field

async def replay(gateway,grant,context,candidate,identity,url,turns,response,guard=None):
    raw=request_bytes(candidate,url,turns)
    if type(response) is not bytes or not 0<len(response)<=jan.LIMIT+16384:
        raise Refused("response_limit")
    def check():
        if guard is not None:
            guard()
    check()
    adapter=LocalAdapter(gateway,context,candidate,identity,"llm",raw,grant)
    r=asyncio.StreamReader(limit=16384)
    r.feed_data(response)
    r.feed_eof()
    sequence=0
    def delta(piece):
        nonlocal sequence
        check()
        sequence+=1
        adapter.event(identity,sequence,"text",piece)
        check()
    try:
        check()
        length,chunked=await jan.response_head(r)
        check()
        # Refuse unframed replays, so trailing data cannot be ignored at DONE.
        if length is None and not chunked:
            raise Refused("explicit_frame_required")
        parts=jan.body_parts(r,length,chunked)
        lines=jan.event_lines(parts)
        guarded=checked_lines(lines,candidate.identity)
        try:
            result=await jan._events(guarded,delta,framed=True)
        finally:
            await guarded.aclose()
            await lines.aclose()
            await parts.aclose()
        if not r.at_eof() or not result.strip():
            raise Refused("invalid_final_response")
        check()
        adapter.event(identity,sequence+1,"completed",None)
        check()
        data=adapter.result()
        check()
        if data!=result.encode("utf-8"):
            raise Refused("result_mismatch")
        return data
    except (Hold,ValueError,EOFError,UnicodeError,RecursionError) as exc:
        adapter.cancel()
        if isinstance(exc,Refused):
            raise
        raise Refused("invalid_jan_response") from None
    except BaseException:
        adapter.cancel()
        raise
