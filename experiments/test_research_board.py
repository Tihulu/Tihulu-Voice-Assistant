import json
import unittest
from dataclasses import replace
from hashlib import sha256
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Request,Refused
from research import canonical
from research_report import Span
from research_board import ResearchBoard,board_target,collect_target,publication_target,export_target

ROOT="https://example.com/docs"
BODY="Mavi <script> örnek".encode()
def headers(body):
    return (("Content-Type","text/plain; charset=utf-8"),("Content-Length",str(len(body))))

class ResearchBoardTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY",False,True)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("external_network",(),"allow"),profiles.Rule("publish_artifact",(),"allow")),clock=profiles.PolicyClock())
        self.n=0
        self.root=self.grant("research",board_target("room",ROOT))
        self.b=ResearchBoard(self.g,self.ctx,"room",ROOT,self.root)
        self.addCleanup(self.b.close)
    def grant(self,action,target):
        self.n+=1
        return self.g.fixture_grant("g"+str(self.n),Request(self.ctx,action,target,"local","public" if action=="research" else "none"),60)
    def consents(self,url,body):
        return self.grant("research",canonical(url).identity),self.grant("research",collect_target(self.b.scope,url,body))
    def collect(self,name="a",body=BODY):
        url=ROOT+"/"+name
        n,s=self.consents(url,body)
        i=self.b.collect(url,("8.8.8.8",),"8.8.8.8",headers(body),body,n,s,confirmed=True)
        return Span(i,sha256(body).hexdigest(),0,len(body.decode())),n,s
    def report(self,identity="report",spans=None):
        if spans is None:
            spans=(self.collect()[0],)
        c=self.b.prepare(identity,spans)
        p=self.grant("publish",publication_target(self.b.scope,c))
        self.b.publish(c,p,confirmed=True)
        return c,p
    def test_multisource_citations_and_exact_export(self):
        a,_,_=self.collect()
        b,_,_=self.collect("b",b"other")
        c,p=self.report(spans=(a,b))
        sources=json.loads(c.payload)["sources"]
        self.assertEqual([x["quote"] for x in sources],[BODY.decode(),"other"])
        self.assertEqual([x["number"] for x in sources],[1,2])
        g=self.grant("publish",export_target(self.b.scope,c,"slot"))
        with self.assertRaises(Refused):
            self.b.export(c.identity,"other",g,confirmed=True)
        self.assertEqual(self.b.export(c.identity,"slot",g,confirmed=True),c.payload)
        with self.assertRaises(Refused):
            self.b.export(c.identity,"slot",g,confirmed=True)
    def test_changed_body_and_confirmation_refused(self):
        url=ROOT+"/a"
        n,s=self.consents(url,BODY)
        for body,confirmed in ((BODY,False),(BODY+b"x",True)):
            with self.assertRaises(Refused):
                self.b.collect(url,("8.8.8.8",),"8.8.8.8",headers(body),body,n,s,confirmed=confirmed)
        self.b.collect(url,("8.8.8.8",),"8.8.8.8",headers(BODY),BODY,n,s,confirmed=True)
    def test_source_grant_is_single_use_and_scope_bound(self):
        url=ROOT+"/a"
        n,s=self.consents(url,BODY)
        root=self.grant("research",board_target("room",ROOT))
        other=ResearchBoard(self.g,self.ctx,"room",ROOT,root)
        self.addCleanup(other.close)
        with self.assertRaises(Refused):
            other.collect(url,("8.8.8.8",),"8.8.8.8",headers(BODY),BODY,n,s,confirmed=True)
        self.b.collect(url,("8.8.8.8",),"8.8.8.8",headers(BODY),BODY,n,s,confirmed=True)
        s2=self.grant("research",collect_target(other.scope,url,BODY))
        with self.assertRaises(Refused):
            other.collect(url,("8.8.8.8",),"8.8.8.8",headers(BODY),BODY,n,s2,confirmed=True)
    def test_revocation_prunes_dependent_report_only(self):
        a,n,s=self.collect()
        b,_,_=self.collect("b")
        first,_=self.report("one",(a,))
        second,_=self.report("two",(b,))
        self.g.revoke(s.identity)
        self.assertEqual(self.b.cards(),(second,))
        with self.assertRaises(Refused):
            self.b.read(first.identity)
    def test_pending_report_and_claim_race(self):
        a,n,s=self.collect()
        c=self.b.prepare("one",(a,))
        p=self.grant("publish",publication_target(self.b.scope,c))
        with self.assertRaises(Refused):
            self.b.publish(replace(c),p,confirmed=True)
        original=self.g.claim
        def revoke(g,r):
            original(g,r)
            if g is p:
                self.g.revoke(n.identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                self.b.publish(c,p,confirmed=True)
        self.assertEqual(self.b.cards(),())
    def test_close_revokes_sources_publication_and_root(self):
        a,n,s=self.collect()
        c,p=self.report(spans=(a,))
        self.b.close()
        for g in (n,s,p,self.root):
            with self.assertRaises(Refused):
                self.g.checkpoint(g,g.request)
        with self.assertRaises(Refused):
            self.b.cards()
    def test_invalid_peer_refuses_and_burns_owned_grants(self):
        url=ROOT+"/a"
        n,s=self.consents(url,BODY)
        with self.assertRaises(Refused):
            self.b.collect(url,("8.8.8.8",),"127.0.0.1",headers(BODY),BODY,n,s,confirmed=True)
        for g in (n,s):
            with self.assertRaises(Refused):
                self.g.checkpoint(g,g.request)
    def test_root_revocation_clears_board(self):
        self.report()
        self.g.revoke(self.root.identity)
        with self.assertRaises(Refused):
            self.b.cards()
        self.assertEqual(self.b._cards,{})
        self.assertEqual(self.b._session._sources,{})

    def test_invalid_span_identity_is_refused_without_retaining_draft(self):
        a,_,_=self.collect()
        with self.assertRaises(Refused):
            self.b.prepare("bad",(Span([],a.digest,0,1),))
        self.assertIsNone(self.b._pending)
        self.b.prepare("good",(a,))

    def test_publication_revoked_during_report_rebuild(self):
        c,p=self.report()
        record=self.b._cards[c.identity]
        builder=record[1]
        original=builder.preview
        def preview():
            result=original()
            self.g.revoke(p.identity)
            return result
        with patch.object(builder,"preview",side_effect=preview):
            with self.assertRaises(Refused):
                self.b.read(c.identity)
        self.assertEqual(self.b.cards(),())

    def test_source_expiry_invalidates_report(self):
        url=ROOT+"/a"
        n=self.g.fixture_grant("short",Request(self.ctx,"research",canonical(url).identity,"local","public"),1)
        s=self.grant("research",collect_target(self.b.scope,url,BODY))
        i=self.b.collect(url,("8.8.8.8",),"8.8.8.8",headers(BODY),BODY,n,s,confirmed=True)
        a=Span(i,sha256(BODY).hexdigest(),0,4)
        c,p=self.report(spans=(a,))
        with patch.object(self.g._clock,"now",return_value=n.deadline):
            self.assertEqual(self.b.cards(),())
        with self.assertRaises(Refused):
            self.b.read(c.identity)
