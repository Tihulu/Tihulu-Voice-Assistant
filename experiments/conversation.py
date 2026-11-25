# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded, route-bound local conversation replay. No network or runtime UI."""
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from gateway import Context, FixtureGateway, Request, Refused, token
from adapter_protocol import prepare, text_bytes
from jan_replay import Turn, request_bytes, replay

MAX_PAIRS=8
MAX_HISTORY_BYTES=12000

@dataclass(frozen=True)
class Draft:
    text: str
    target: str
    identity: str

def session_target(candidate,url,identity):
    raw=request_bytes(candidate,url,(Turn("user","validation"),))
    call=prepare(candidate,"validation","llm",raw)
    return sha256(("conversation:"+token(identity)+":"+call.target).encode("ascii")).hexdigest()

class Conversation:
    """Owner reviews one draft, grants one exact history-bound local turn."""
    def __init__(self,gateway,context,candidate,url,identity,session_grant):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context!=context or context.mode=="CLOUD_CHAT"):
            raise Refused("local_context_required")
        req=Request(context,"engine",session_target(candidate,url,identity),"local")
        gateway.claim(session_grant,req)
        try:
            gateway.checkpoint(session_grant,req)
        except BaseException:
            gateway.revoke(session_grant.identity)
            raise
        self._g,self._ctx,self._candidate,self._url=gateway,context,candidate,url
        self._identity=identity
        self._history=()
        self._checks=[(session_grant,req)]
        self._draft=None
        self._pending=None
        self._counter=0
        self._state="ready"
        self._lock=RLock()

    @property
    def state(self):
        with self._lock:
            return self._state

    def _clear(self,state):
        if self._pending is not None:
            self._g.revoke(self._pending[0].identity)
        for grant,_ in self._checks:
            self._g.revoke(grant.identity)
        self._checks.clear()
        self._history=()
        self._draft=self._pending=None
        self._state=state

    def _live(self):
        if self._state in ("closed","held"):
            raise Refused("conversation_unavailable")
        try:
            for grant,req in self._checks:
                self._g.checkpoint(grant,req)
            if self._pending is not None:
                self._g.checkpoint(*self._pending)
        except Refused:
            self._clear("held")
            raise

    def history(self):
        with self._lock:
            self._live()
            return self._history

    def draft(self,text):
        with self._lock:
            self._live()
            if self._state not in ("ready","review"):
                raise Refused("conversation_busy")
            if not text_bytes(text).strip():
                raise Refused("empty_draft")
            if len(self._history)>=MAX_PAIRS*2 or self._counter>=64:
                raise Refused("conversation_limit")
            turns=(*self._history,Turn("user",text))
            raw=request_bytes(self._candidate,self._url,turns)
            if sum(len(text_bytes(t.text)) for t in turns)>MAX_HISTORY_BYTES:
                raise Refused("history_limit")
            self._counter+=1
            identity=sha256((self._identity+":"+str(self._counter)).encode("ascii")).hexdigest()
            self._draft=Draft(text,prepare(self._candidate,identity,"llm",raw).target,identity)
            self._state="review"
            return self._draft

    def discard_draft(self):
        with self._lock:
            self._live()
            if self._state!="review":
                raise Refused("no_current_draft")
            self._draft=None
            self._state="ready"

    async def submit(self,draft,grant,response,*,confirmed=False):
        with self._lock:
            self._live()
            if self._state!="review" or draft is not self._draft or confirmed is not True:
                raise Refused("explicit_current_draft_required")
            req=Request(self._ctx,"engine",draft.target,"local")
            self._g.checkpoint(grant,req)
            turns=(*self._history,Turn("user",draft.text))
            self._pending=(grant,req)
            self._state="running"
        try:
            data=await replay(self._g,grant,self._ctx,self._candidate,draft.identity,self._url,turns,response)
            with self._lock:
                self._live()
                if self._state!="running" or self._draft is not draft:
                    raise Refused("stale_turn")
                text=data.decode("utf-8")
                if not text_bytes(text).strip():
                    raise Refused("empty_answer")
                history=(*turns,Turn("assistant",text))
                if sum(len(text_bytes(t.text)) for t in history)>MAX_HISTORY_BYTES:
                    raise Refused("history_limit")
                self._checks.append(self._pending)
                self._pending=None
                self._history=history
                self._draft=None
                self._state="ready"
                return text
        except BaseException:
            with self._lock:
                if self._state!="closed":
                    self._clear("held")
            raise

    def poll(self):
        with self._lock:
            if self._state not in ("closed","held"):
                try:
                    self._live()
                except Refused:
                    pass
            return self._state

    def close(self):
        with self._lock:
            self._clear("closed")
