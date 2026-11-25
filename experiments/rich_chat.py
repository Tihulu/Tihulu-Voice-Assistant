# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping rich chat result model. No GTK, filesystem, network or model I/O."""
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
import csv
import io
import json
import re
from gateway import Context, FixtureGateway, Request, Refused, token
from tihulu_jarvis.temperature_engine import analyze_temperature, render_svg
from tihulu_jarvis.analysis_export import summary_rows
from tihulu_jarvis.files import checked_query, MAX_MATCHES, MAX_SNIPPET
from tihulu_jarvis.voice.m1 import Hold

MAX_CARDS=16
MAX_TOTAL=1048576
MAX_INPUT=262144
KINDS=("text","temperature","search")

def digest(data):
    return sha256(data).hexdigest()

def key(*parts):
    return digest(json.dumps(parts,ensure_ascii=True,separators=(",",":")).encode("ascii"))

def room_target(identity):
    return key("rich-chat",token(identity))

def room_scope(identity,grant_identity):
    return key("rich-instance",token(identity),token(grant_identity))

def query_value(kind,query):
    if kind=="search":
        if type(query) is not str:
            raise Refused("invalid_query")
        try:
            query=checked_query(query)
        except Hold:
            raise Refused("invalid_query") from None
        if any(127<=ord(c)<=159 or 0xd800<=ord(c)<=0xdfff for c in query):
            raise Refused("invalid_query")
        return query
    if query is not None:
        raise Refused("unexpected_query")
    return None

def source_target(identity,kind,data,*,room="room",query=None):
    if (kind not in KINDS or type(data) is not bytes or not 0<len(data)<=MAX_INPUT):
        raise Refused("invalid_source")
    return key("rich-source",token(room),token(identity),kind,digest(data),query_value(kind,query))

@dataclass(frozen=True)
class Part:
    name: str
    mime: str
    data: bytes
    digest: str

@dataclass(frozen=True)
class Card:
    identity: str
    kind: str
    source_digest: str
    parts: tuple
    digest: str

def part(name,mime,data):
    return Part(name,mime,data,digest(data))

def build(identity,kind,data,*,cancelled=lambda:False,color="blue",query=None):
    source_target(identity,kind,data,query=query)
    query=query_value(kind,query)
    if kind in ("text","search"):
        try:
            text=data.decode("utf-8")
        except UnicodeError:
            raise Refused("invalid_utf8") from None
        if any((ord(c)<32 and c not in "\n\t\r") or 127<=ord(c)<=159 for c in text):
            raise Refused("invalid_text")
        if kind=="text":
            parts=(part("preview","text/plain",data),)
        else:
            pattern=re.compile(re.escape(query),re.IGNORECASE)
            matches=[]
            total=0
            for number,line in enumerate(text.splitlines(),1):
                if number%128==1 and cancelled():
                    raise Refused("search_cancelled")
                hit=pattern.search(line)
                if hit is None:
                    continue
                total+=1
                if len(matches)<MAX_MATCHES:
                    start=max(0,hit.start()-40)
                    end=min(len(line),start+MAX_SNIPPET)
                    snippet=("…" if start else "")+line[start:end]+("…" if end<len(line) else "")
                    matches.append({"line":number,"snippet":snippet})
            if cancelled():
                raise Refused("search_cancelled")
            result=json.dumps({"query":query,"matching_lines":total,"shown":len(matches),
                               "truncated":total>len(matches),"matches":matches},
                              ensure_ascii=False,separators=(",",":")).encode("utf-8")
            parts=(part("matches","application/json",result),)
    else:
        try:
            series=analyze_temperature(data,cancelled=cancelled)
            svg=render_svg(series,color=color,cancelled=cancelled)
        except Hold:
            raise Refused("invalid_analysis") from None
        rows=summary_rows(series)
        summary=json.dumps({"columns":["metric","value","unit"],"rows":rows},
                           separators=(",",":"),allow_nan=False).encode("ascii")
        stream=io.StringIO(newline="")
        csv.writer(stream,lineterminator="\n").writerows((("time_s","temperature_c"),*series.points))
        parts=(part("table","application/json",summary),
               part("chart","image/svg+xml",svg),
               part("data","text/csv",stream.getvalue().encode("ascii")))
    total=sum(len(p.data) for p in parts)
    if total>MAX_TOTAL:
        raise Refused("result_limit")
    d=key("rich-card",identity,kind,digest(data),tuple((p.name,p.mime,p.digest) for p in parts))
    return Card(identity,kind,digest(data),parts,d)

def publish_target(room,card):
    if type(card) is not Card:
        raise Refused("invalid_card")
    return key("rich-publish",token(room),card.identity,card.digest)

def export_target(room,card,name,destination):
    if type(card) is not Card:
        raise Refused("invalid_card")
    p=next((p for p in card.parts if p.name==name),None)
    if p is None:
        raise Refused("unknown_part")
    return key("rich-export",token(room),card.identity,card.digest,p.name,p.digest,token(destination))

class RichChat:
    """One owned context. Cards are local-only, never inserted into model history."""
    def __init__(self,gateway,context,identity,grant):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context!=context or context.mode=="CLOUD_CHAT"):
            raise Refused("local_context_required")
        self._g,self._ctx,self._id=gateway,context,token(identity)
        self._lock=RLock()
        self._cards={}
        self._seen=set()
        self._pending=None
        self._closed=False
        req=Request(context,"publish",room_target(identity),"local")
        gateway.claim(grant,req)
        try:
            gateway.checkpoint(grant,req)
        except BaseException:
            gateway.revoke(grant.identity)
            raise
        self._room=(grant,req)
        self.scope=room_scope(self._id,grant.identity)

    def _live(self):
        if self._closed:
            raise Refused("chat_closed")
        try:
            self._g.checkpoint(*self._room)
        except Refused:
            self.close()
            raise

    def _check_card(self,identity):
        self._live()
        token(identity)
        record=self._cards.get(identity)
        if record is None:
            raise Refused("unknown_card")
        card,checks=record
        try:
            for grant,req in checks:
                self._g.checkpoint(grant,req)
            if self._closed or self._cards.get(identity) is not record:
                raise Refused("stale_card")
        except Refused:
            if self._cards.pop(identity,None) is record:
                for grant,_ in checks:
                    self._g.revoke(grant.identity)
            raise
        return card

    def _drop_pending(self):
        if self._pending is not None:
            _,grant,_=self._pending
            self._pending=None
            self._g.revoke(grant.identity)

    def prepare(self,identity,kind,data,source_grant,*,color="blue",query=None):
        """Compute one private draft after exact source consent; never publish automatically."""
        with self._lock:
            self._live()
            token(identity)
            self.poll()
            if self._pending is not None:
                raise Refused("draft_pending")
            if identity in self._seen or len(self._seen)>=64 or len(self._cards)>=MAX_CARDS:
                raise Refused("card_limit_or_duplicate")
            req=Request(self._ctx,"engine",source_target(identity,kind,data,room=self.scope,query=query),"local","private")
            self._g.claim(source_grant,req)
            self._seen.add(identity)
            try:
                def checkpoint():
                    self._live()
                    self._g.checkpoint(source_grant,req)
                    return False
                checkpoint()
                card=build(identity,kind,data,cancelled=checkpoint,color=color,query=query)
                checkpoint()
                total=sum(len(p.data) for c,_ in self._cards.values() for p in c.parts)
                if total+sum(len(p.data) for p in card.parts)>MAX_TOTAL:
                    raise Refused("chat_bytes_limit")
                self._pending=(card,source_grant,req)
                return card
            except BaseException:
                self._g.revoke(source_grant.identity)
                raise

    def publish(self,card,publish_grant,*,confirmed=False):
        with self._lock:
            self._live()
            if confirmed is not True or self._pending is None or card is not self._pending[0]:
                raise Refused("explicit_current_card_required")
            record=self._pending
            _,source_grant,req=record
            pub=Request(self._ctx,"publish",publish_target(self.scope,card),"local","private")
            claimed=False
            try:
                self._g.checkpoint(source_grant,req)
                self._g.claim(publish_grant,pub)
                claimed=True
                self._g.checkpoint(source_grant,req)
                self._g.checkpoint(publish_grant,pub)
                self._live()
                if self._pending is not record:
                    raise Refused("stale_draft")
                self._cards[card.identity]=(card,((source_grant,req),(publish_grant,pub)))
                self._pending=None
                return card
            except BaseException:
                if claimed:
                    self._g.revoke(publish_grant.identity)
                self._drop_pending()
                raise

    def discard(self):
        with self._lock:
            self._live()
            if self._pending is None:
                raise Refused("no_pending_card")
            self._drop_pending()

    def add(self,identity,kind,data,source_grant,publish_grant,*,color="blue",query=None):
        """Trusted fixture convenience for pre-authorized exact output."""
        with self._lock:
            card=self.prepare(identity,kind,data,source_grant,color=color,query=query)
            return self.publish(card,publish_grant,confirmed=True)

    def poll(self):
        with self._lock:
            self._live()
            if self._pending is not None:
                _,grant,req=self._pending
                try:
                    self._g.checkpoint(grant,req)
                except Refused:
                    self._drop_pending()
            return self.cards()

    def cards(self):
        with self._lock:
            self._live()
            current=[]
            for identity in tuple(self._cards):
                try:
                    current.append(self._check_card(identity))
                except Refused:
                    self._live()
            return tuple(current)

    def read(self,identity):
        with self._lock:
            return self._check_card(identity)

    def export(self,identity,name,destination,grant,*,confirmed=False):
        with self._lock:
            if confirmed is not True:
                raise Refused("explicit_export_required")
            card=self._check_card(identity)
            target=export_target(self.scope,card,name,destination)
            req=Request(self._ctx,"publish",target,"local","private")
            self._g.claim(grant,req)
            try:
                if self._check_card(identity) is not card:
                    raise Refused("stale_card")
                self._g.checkpoint(grant,req)
                if self._check_card(identity) is not card:
                    raise Refused("stale_card")
                self._g.checkpoint(grant,req)
                return next(p.data for p in card.parts if p.name==name)
            except BaseException:
                self._g.revoke(grant.identity)
                raise

    def clear(self,identity):
        with self._lock:
            self._live()
            token(identity)
            record=self._cards.pop(identity,None)
            if record is None:
                raise Refused("unknown_card")
            for grant,_ in record[1]:
                self._g.revoke(grant.identity)

    def close(self):
        with self._lock:
            self._closed=True
            self._drop_pending()
            for _,checks in self._cards.values():
                for grant,_ in checks:
                    self._g.revoke(grant.identity)
            self._cards.clear()
            self._g.revoke(self._room[0].identity)
