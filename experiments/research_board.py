# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-scoped public research result board, synthetic input only."""
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from gateway import Context, FixtureGateway, Request, Refused, token
from research import canonical, scoped
from research_session import ResearchSession
from research_report import ReportBuilder, Span

def key(*values):
    return sha256(json.dumps(values,ensure_ascii=True,separators=(",",":")).encode("ascii")).hexdigest()

def board_target(room,root):
    return key("research-board",token(room),canonical(root).identity)

def board_scope(room,grant_identity):
    return key("research-instance",token(room),token(grant_identity))

def collect_target(scope,url,body):
    if type(body) is not bytes or not 0<len(body)<=65536:
        raise Refused("invalid_body")
    return key("research-snapshot",token(scope),canonical(url).identity,sha256(body).hexdigest())

@dataclass(frozen=True)
class Card:
    identity: str
    digest: str
    payload: bytes

def publication_target(scope,card):
    if type(card) is not Card:
        raise Refused("invalid_card")
    return key("research-publication",token(scope),token(card.identity),card.digest)

def export_target(scope,card,destination):
    return key("research-export",publication_target(scope,card),token(destination))

class ResearchBoard:
    """Source grants and report grants are separate; no automatic context injection."""
    def __init__(self,gateway,context,room,root,grant):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context!=context or context.mode=="CLOUD_CHAT"
                or context.offline or (context.mode=="LOCAL_ONLY" and not context.public_opt_in)):
            raise Refused("public_research_disabled")
        req=Request(context,"research",board_target(room,root),"local","public")
        gateway.claim(grant,req)
        self._g,self._ctx=gateway,context
        self._root=(grant,req)
        self.scope=board_scope(room,grant.identity)
        self._session=ResearchSession(gateway,context,root)
        self._sources={}
        self._pending=None
        self._cards={}
        self._seen=set()
        self._closed=False
        self._lock=RLock()
        try:
            self._live()
        except BaseException:
            self.close()
            raise

    def _live(self):
        if self._closed:
            raise Refused("board_closed")
        try:
            self._g.checkpoint(*self._root)
        except Refused:
            self.close()
            raise

    def _checks(self,identities):
        self._live()
        for identity in identities:
            if identity not in self._sources:
                raise Refused("unknown_source")
            for grant,req in self._sources[identity]:
                self._g.checkpoint(grant,req)

    def collect(self,url,resolved,peer,headers,body,network_grant,snapshot_grant,*,confirmed=False):
        with self._lock:
            self._live()
            if confirmed is not True:
                raise Refused("explicit_source_required")
            target=canonical(url)
            if not scoped(target,canonical(self._session._root)):
                raise Refused("outside_scope")
            if target.identity in self._sources or len(self._sources)>=8:
                raise Refused("source_limit_or_duplicate")
            nreq=Request(self._ctx,"research",target.identity,"local","public")
            sreq=Request(self._ctx,"research",collect_target(self.scope,url,body),"local","public")
            self._g.checkpoint(network_grant,nreq)
            self._g.checkpoint(snapshot_grant,sreq)
            claimed=[]
            try:
                self._g.claim(snapshot_grant,sreq)
                claimed.append(snapshot_grant)
                self._g.claim(network_grant,nreq)
                claimed.append(network_grant)
                identity=self._session.collect(network_grant,url,resolved,peer,headers,body)
                self._live()
                self._g.checkpoint(network_grant,nreq)
                self._g.checkpoint(snapshot_grant,sreq)
                self._sources[identity]=((network_grant,nreq),(snapshot_grant,sreq))
                return identity
            except BaseException:
                for grant in claimed:
                    self._g.revoke(grant.identity)
                # A partially inserted source cannot be reused.
                if target.identity in self._session._sources:
                    self.close()
                raise

    def _verify(self,record):
        card,builder,identities,checks=record
        self._checks(identities)
        for grant,req in checks:
            self._g.checkpoint(grant,req)
        report=builder.preview()
        if report.digest!=card.digest or report.payload!=card.payload:
            raise Refused("report_changed")
        self._checks(identities)
        self._live()
        return card

    def prepare(self,identity,spans):
        with self._lock:
            self._live()
            token(identity)
            if self._pending is not None or identity in self._seen or len(self._seen)>=16 or len(self._cards)>=8:
                raise Refused("report_limit_or_pending")
            builder=ReportBuilder(self._g,self._ctx,self._session,spans)
            ids=tuple(dict.fromkeys(s.identity for s in spans))
            try:
                self._checks(ids)
                report=builder.preview()
                card=Card(identity,report.digest,report.payload)
                self._checks(ids)
                self._pending=(card,builder,ids,())
                self._seen.add(identity)
                return card
            except BaseException:
                builder.close()
                raise

    def _drop(self,record):
        _,builder,_,checks=record
        builder.close()
        for grant,_ in checks:
            self._g.revoke(grant.identity)

    def discard(self):
        with self._lock:
            self._live()
            if self._pending is None:
                raise Refused("no_pending_report")
            self._drop(self._pending)
            self._pending=None

    def publish(self,card,grant,*,confirmed=False):
        with self._lock:
            self._live()
            record=self._pending
            if confirmed is not True or record is None or card is not record[0]:
                raise Refused("explicit_current_report_required")
            req=Request(self._ctx,"publish",publication_target(self.scope,card),"local")
            claimed=False
            try:
                self._verify(record)
                self._g.claim(grant,req)
                claimed=True
                self._verify(record)
                self._g.checkpoint(grant,req)
                if self._pending is not record:
                    raise Refused("stale_report")
                self._cards[card.identity]=(*record[:3],((grant,req),))
                self._pending=None
                return card
            except BaseException:
                if claimed:
                    self._g.revoke(grant.identity)
                if self._pending is record:
                    self._drop(record)
                    self._pending=None
                raise

    def read(self,identity):
        with self._lock:
            self._live()
            record=self._cards.get(token(identity))
            if record is None:
                raise Refused("unknown_report")
            try:
                result=self._verify(record)
                if self._cards.get(identity) is not record:
                    raise Refused("stale_report")
                return result
            except Refused:
                if self._cards.pop(identity,None) is record:
                    self._drop(record)
                raise

    def cards(self):
        with self._lock:
            self._live()
            if self._pending is not None:
                try:
                    self._verify(self._pending)
                except Refused:
                    if self._pending is not None:
                        self._drop(self._pending)
                        self._pending=None
                    self._live()
            result=[]
            for identity in tuple(self._cards):
                try:
                    result.append(self.read(identity))
                except Refused:
                    self._live()
            return tuple(result)

    def export(self,identity,destination,grant,*,confirmed=False):
        with self._lock:
            if confirmed is not True:
                raise Refused("explicit_export_required")
            card=self.read(identity)
            req=Request(self._ctx,"publish",export_target(self.scope,card,destination),"local")
            self._g.claim(grant,req)
            try:
                if self.read(identity) is not card:
                    raise Refused("stale_report")
                self._g.checkpoint(grant,req)
                if self.read(identity) is not card:
                    raise Refused("stale_report")
                return card.payload
            except BaseException:
                self._g.revoke(grant.identity)
                raise

    def clear(self,identity):
        with self._lock:
            self._live()
            record=self._cards.pop(token(identity),None)
            if record is None:
                raise Refused("unknown_report")
            self._drop(record)

    def close(self):
        with self._lock:
            self._closed=True
            if self._pending is not None:
                self._drop(self._pending)
                self._pending=None
            for record in self._cards.values():
                self._drop(record)
            self._cards.clear()
            for checks in self._sources.values():
                for grant,_ in checks:
                    self._g.revoke(grant.identity)
            self._sources.clear()
            self._session.close()
            self._g.revoke(self._root[0].identity)
