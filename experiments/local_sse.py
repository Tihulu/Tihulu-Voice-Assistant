# SPDX-License-Identifier: AGPL-3.0-only
"""Closed synthetic OpenAI-style SSE subset for local LLM text only.

No endpoint, credential, network, model invocation or claim of Jan wire
compatibility. The trusted fixture binds response identity independently.
"""
import json
from threading import Lock
from gateway import Refused
from adapter_protocol import LocalAdapter

MAX_STREAM=131072
MAX_LINE=32768

def strict_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise Refused("duplicate_sse_field")
        result[key]=value
    return result

class LocalSSE:
    def __init__(self,adapter,response_id):
        from gateway import token
        if type(adapter) is not LocalAdapter or adapter.call.operation!="llm":
            raise Refused("local_llm_required")
        if type(response_id) is not str:
            raise Refused("invalid_response_identity")
        token(response_id)
        self._adapter=adapter
        self._response_id=response_id
        self._buffer=bytearray()
        self._total=0
        self._sequence=0
        self._pending=None
        self._finish=False
        self._done=False
        self._closed=False
        self._owner=object()
        self._attached=False
        self._lock=Lock()

    def _event(self,kind,value):
        self._sequence+=1
        self._adapter.stream_event(self._owner,self._adapter.call.identity,self._sequence,kind,value)

    def _line(self,line):
        # Closed subset: exactly one data line per SSE event, followed by
        # the blank separator. Do not act on an unterminated data line.
        if not line:
            if self._pending is not None:
                raw=self._pending
                self._pending=None
                self._dispatch(raw)
            return
        if self._done or self._pending is not None or not line.startswith(b"data: "):
            raise Refused("invalid_sse_line")
        self._pending=line[6:]

    def _dispatch(self,raw):
        if raw==b"[DONE]":
            if not self._finish:
                raise Refused("missing_finish")
            self._done=True
            return
        if self._finish:
            raise Refused("late_sse_data")
        try:
            obj=json.loads(raw.decode("utf-8"),object_pairs_hook=strict_object)
        except (ValueError,UnicodeError,RecursionError):
            raise Refused("invalid_sse_json") from None
        if (type(obj) is not dict or set(obj)!={"id","model","choices"}
                or obj["id"]!=self._response_id or obj["model"]!=self._adapter.call.model
                or type(obj["choices"]) is not list or len(obj["choices"])!=1):
            raise Refused("sse_response_mismatch")
        choice=obj["choices"][0]
        if (type(choice) is not dict or set(choice)!={"index","delta","finish_reason"}
                or type(choice["index"]) is not int or choice["index"]!=0
                or type(choice["delta"]) is not dict):
            raise Refused("invalid_sse_choice")
        delta=choice["delta"]
        reason=choice["finish_reason"]
        if reason is None and set(delta)=={"content"}:
            self._event("text",delta["content"])
        elif reason=="stop" and not delta:
            self._finish=True
        else:
            raise Refused("unsupported_sse_delta")

    def _start_locked(self):
        if self._closed:
            raise Refused("terminal_sse")
        if not self._attached:
            self._adapter.attach_fixture_stream(self._owner)
            self._attached=True

    def start(self):
        with self._lock:
            self._start_locked()

    def feed(self,data):
        with self._lock:
            self._start_locked()
            try:
                self._adapter.checkpoint_fixture_stream(self._owner)
                if type(data) is not bytes or not data or self._total+len(data)>MAX_STREAM:
                    raise Refused("sse_stream_limit")
                self._total+=len(data)
                self._buffer.extend(data)
                while b"\n" in self._buffer:
                    raw,_,rest=self._buffer.partition(b"\n")
                    self._buffer=bytearray(rest)
                    if len(raw)>MAX_LINE:
                        raise Refused("sse_line_limit")
                    self._line(bytes(raw).removesuffix(b"\r"))
                if len(self._buffer)>MAX_LINE:
                    raise Refused("sse_line_limit")
                self._adapter.checkpoint_fixture_stream(self._owner)
            except Refused:
                self._abort_locked()
                raise

    def finish(self):
        with self._lock:
            self._start_locked()
            try:
                self._adapter.checkpoint_fixture_stream(self._owner)
                if self._buffer or self._pending is not None or not self._done or not self._finish:
                    raise Refused("truncated_sse")
                self._event("completed",None)
                result=self._adapter.result()
                self._closed=True
                return result
            except Refused:
                self._abort_locked()
                raise

    def _abort_locked(self):
        if self._closed:
            return
        self._buffer.clear()
        self._pending=None
        self._closed=True
        if self._attached:
            self._adapter.abort_fixture_stream(self._owner)

    def abort(self):
        with self._lock:
            self._abort_locked()
