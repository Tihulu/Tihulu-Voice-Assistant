# SPDX-License-Identifier: AGPL-3.0-only
"""Unified local chat fixture; no GTK, model execution, devices or host I/O."""
from dataclasses import dataclass
from threading import RLock
import json
from gateway import Context, FixtureGateway, Request, Refused, token
from conversation import Conversation, session_target
from rich_chat import RichChat, Card, room_target, key
from adapter_protocol import text_bytes
from voice_review import review, target as stt_target

@dataclass(frozen=True)
class Item:
    sequence: int
    kind: str
    value: object

def identities(identity):
    token(identity)
    return key("conversation",identity),key("results",identity)

def workspace_target(identity,candidate,url):
    chat,room=identities(identity)
    return key("workspace",identity,session_target(candidate,url,chat),room_target(room))

def attachment_target(workspace,card,name,question):
    if type(card) is not Card or type(name) is not str:
        raise Refused("invalid_attachment")
    part=next((p for p in card.parts if p.name==name),None)
    if (part is None or part.mime not in ("text/plain","application/json","text/csv")
            or len(part.data)>4096 or not text_bytes(question).strip()):
        raise Refused("invalid_attachment")
    return key("local-attachment",token(workspace),card.identity,card.digest,
               part.name,part.digest,question)

class Workspace:
    """Owns both child lifetimes; cards never become automatic model messages."""
    def __init__(self,gateway,context,identity,candidate,url,grant,chat_grant,room_grant):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context!=context or context.mode=="CLOUD_CHAT"):
            raise Refused("local_context_required")
        self._g,self._ctx=gateway,context
        self._id=token(identity)
        self._lock=RLock()
        self._state="ready"
        self._chat=self._rich=None
        self._voice=None
        self._voice_checks=[]
        self._attachment=None
        self._attachment_checks=[]
        self._timeline=[]
        self._counter=0
        self._sequence=0
        req=Request(context,"engine",workspace_target(identity,candidate,url),"local")
        gateway.claim(grant,req)
        self._root=(grant,req)
        chat,room=identities(identity)
        try:
            gateway.checkpoint(*self._root)
            self._chat=Conversation(gateway,context,candidate,url,chat,chat_grant)
            self._rich=RichChat(gateway,context,room,room_grant)
            self._live()
        except BaseException:
            self._clear("held")
            raise

    @property
    def state(self):
        with self._lock:
            return self._state

    def _drop_voice(self):
        if self._voice is not None:
            _,_,grant,_=self._voice
            self._voice=None
            self._g.revoke(grant.identity)

    def _drop_attachment(self):
        if self._attachment is not None:
            _,_,grant,_=self._attachment
            self._attachment=None
            self._g.revoke(grant.identity)

    def _check_attachment(self,record):
        _,card,grant,req=record
        self._g.checkpoint(grant,req)
        if self._rich.read(card.identity) is not card:
            raise Refused("stale_attachment")

    def _clear(self,state):
        self._state=state
        self._timeline.clear()
        self._drop_voice()
        self._drop_attachment()
        try:
            for grant,_ in self._voice_checks:
                self._g.revoke(grant.identity)
            for _,_,grant,_ in self._attachment_checks:
                self._g.revoke(grant.identity)
        finally:
            self._voice_checks.clear()
            self._attachment_checks.clear()
            try:
                if self._chat is not None:
                    self._chat.close()
            finally:
                try:
                    if self._rich is not None:
                        self._rich.close()
                finally:
                    self._g.revoke(self._root[0].identity)

    def _live(self):
        if self._state in ("held","closed"):
            raise Refused("workspace_unavailable")
        try:
            self._g.checkpoint(*self._root)
            for grant,req in self._voice_checks:
                self._g.checkpoint(grant,req)
            if self._voice is not None:
                _,_,grant,req=self._voice
                self._g.checkpoint(grant,req)
            if self._chat is not None and self._chat.poll() in ("held","closed"):
                raise Refused("conversation_unavailable")
            if self._rich is not None:
                self._rich.poll()
                for record in self._attachment_checks:
                    self._check_attachment(record)
                if self._attachment is not None:
                    self._check_attachment(self._attachment)
            if self._state in ("held","closed"):
                raise Refused("workspace_unavailable")
        except Refused:
            if self._state!="closed":
                self._clear("held")
            raise

    def _ready(self):
        self._live()
        if self._state!="ready":
            raise Refused("workspace_busy")

    def _append(self,kind,value):
        self._sequence+=1
        self._timeline.append(Item(self._sequence,kind,value))

    def draft(self,text):
        with self._lock:
            self._ready()
            d=self._chat.draft(text)
            self._drop_voice()
            self._drop_attachment()
            self._live()
            return d

    def voice_identity(self):
        with self._lock:
            self._ready()
            if self._counter>=32:
                raise Refused("voice_limit")
            return key("voice",self._id,self._counter+1)

    def voice_draft(self,candidate,pcm,grant,events):
        with self._lock:
            self._ready()
            identity=self.voice_identity()
            req=Request(self._ctx,"engine",stt_target(candidate,identity,pcm),"local")
            self._g.checkpoint(grant,req)
            self._counter+=1
            try:
                voice=review(self._g,self._ctx,candidate,identity,pcm,grant,events)
                self._live()
                self._g.checkpoint(grant,req)
                draft=self._chat.draft(voice.text)
                self._drop_voice()
                self._drop_attachment()
                self._voice=(voice,draft,grant,req)
                self._live()
                return draft
            except BaseException:
                self._g.revoke(grant.identity)
                raise

    def edit_voice(self,draft,text):
        with self._lock:
            self._ready()
            if self._voice is None or draft is not self._voice[1]:
                raise Refused("stale_voice_draft")
            voice,_,grant,req=self._voice
            edited=self._chat.draft(text)
            self._voice=(voice,edited,grant,req)
            self._live()
            return edited

    def discard_draft(self):
        with self._lock:
            self._ready()
            self._chat.discard_draft()
            self._drop_voice()
            self._drop_attachment()

    def attach(self,identity,name,question,grant,*,confirmed=False):
        """Explicit local-only selection; attachment remains data, never tool authority."""
        with self._lock:
            self._ready()
            if confirmed is not True:
                raise Refused("explicit_attachment_required")
            card=self._rich.read(identity)
            target=attachment_target(self._id,card,name,question)
            req=Request(self._ctx,"engine",target,"local","private")
            self._g.claim(grant,req)
            try:
                part=next(p for p in card.parts if p.name==name)
                raw=json.dumps({"question":question,"local_evidence":{
                    "card":card.identity,"source_digest":card.source_digest,
                    "part":part.name,"untrusted_text":part.data.decode("utf-8")}},
                    ensure_ascii=False,separators=(",",":"))
                self._g.checkpoint(grant,req)
                self._rich.read(identity)
                draft=self._chat.draft(raw)
                self._drop_voice()
                self._drop_attachment()
                self._attachment=(draft,card,grant,req)
                self._live()
                return draft
            except BaseException:
                self._g.revoke(grant.identity)
                raise

    async def submit(self,draft,grant,response,*,confirmed=False):
        with self._lock:
            self._ready()
            if confirmed is not True:
                raise Refused("explicit_confirmation_required")
            if self._voice is not None and draft is not self._voice[1]:
                raise Refused("stale_voice_draft")
            if self._attachment is not None and draft is not self._attachment[0]:
                raise Refused("stale_attachment_draft")
            self._state="running"
        try:
            answer=await self._chat.submit(draft,grant,response,confirmed=True)
            with self._lock:
                self._live()
                if self._state!="running":
                    raise Refused("stale_turn")
                if self._voice is not None:
                    _,_,sg,sr=self._voice
                    self._voice_checks.append((sg,sr))
                    self._voice=None
                if self._attachment is not None:
                    self._attachment_checks.append(self._attachment)
                    self._attachment=None
                self._append("user",draft.text)
                self._append("assistant",answer)
                self._state="ready"
                return answer
        except BaseException:
            with self._lock:
                if self._state=="running":
                    if self._chat.state=="review":
                        self._state="ready"
                    else:
                        self._clear("held")
            raise

    def prepare_card(self,identity,kind,data,grant,**options):
        with self._lock:
            self._ready()
            result=self._rich.prepare(identity,kind,data,grant,**options)
            self._live()
            return result

    def publish_card(self,card,grant,*,confirmed=False):
        with self._lock:
            self._ready()
            result=self._rich.publish(card,grant,confirmed=confirmed)
            self._live()
            self._append("card",result.identity)
            return result

    def discard_card(self):
        with self._lock:
            self._ready()
            self._rich.discard()

    def clear_card(self,identity):
        with self._lock:
            self._ready()
            self._rich.clear(identity)
            self._timeline=[item for item in self._timeline
                            if not (item.kind=="card" and item.value==identity)]
            # If already used as model evidence, losing the source invalidates
            # the whole retained conversation, rather than retaining derived text.
            self._live()

    def export_card(self,identity,name,destination,grant,*,confirmed=False):
        with self._lock:
            self._ready()
            data=self._rich.export(identity,name,destination,grant,confirmed=confirmed)
            self._live()
            self._rich.read(identity)
            return data

    def timeline(self):
        with self._lock:
            self._live()
            cards={c.identity:c for c in self._rich.cards()}
            self._timeline=[item for item in self._timeline
                            if item.kind!="card" or item.value in cards]
            self._live()
            current=[]
            for item in self._timeline:
                if item.kind=="card":
                    try:
                        current.append(Item(item.sequence,item.kind,self._rich.read(item.value)))
                    except Refused:
                        self._live()
                else:
                    current.append(item)
            return tuple(current)

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
