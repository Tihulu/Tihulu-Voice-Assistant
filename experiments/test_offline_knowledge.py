import unittest
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from offline_knowledge import OfflineIndex,document_target

class OfflineKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.i=OfflineIndex(self.g,self.ctx)
        self.data="Mavi\nGrüße aus Köln\nİstanbul\nStraße".encode()
    def grant(self,identity,data,gid):
        return self.g.fixture_grant(gid,Request(self.ctx,"engine",document_target(identity,data),"local","private"),60)
    def add(self,identity,data,gid):
        self.i.add(identity,data,self.grant(identity,data,gid))
    def test_multilingual_search_preserves_original_evidence(self):
        self.add("doc",self.data,"a")
        self.assertEqual(self.i.search("MAVI")[0].excerpt,"Mavi")
        hit=self.i.search("STRASSE")[0]
        self.assertEqual((hit.document,hit.digest,hit.line,hit.excerpt),("doc",sha256(self.data).hexdigest(),4,"Straße"))
        self.assertEqual(self.i.search("Gru\u0308ße")[0].line,2)
    def test_exact_document_grant_and_revocation(self):
        g=self.grant("doc",self.data,"a")
        with self.assertRaises(Refused):
            self.i.add("doc",b"changed",g)
        self.i.add("doc",self.data,g)
        self.add("other",b"mavi","b")
        self.g.revoke("a")
        self.assertEqual([h.document for h in self.i.search("mavi")],["other"])
        self.assertNotIn("doc",self.i._docs)
    def test_bounds_and_close(self):
        for n in range(16):
            self.add("doc"+str(n),b"mavi","g"+str(n))
        with self.assertRaises(Refused):
            self.add("extra",b"mavi","extra")
        self.i.close()
        with self.assertRaises(Refused):
            self.i.search("mavi")
        self.assertEqual(self.i._bytes,0)
    def test_invalid_text_and_no_grant_reuse(self):
        g=self.grant("doc",self.data,"a")
        self.i.add("doc",self.data,g)
        other=OfflineIndex(self.g,self.ctx)
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            other.add("doc",self.data,g)
        for n,data in enumerate((b"\xff",b"\x00",b"x\ny"*4097)):
            with self.assertRaises(Refused):
                self.add("bad"+str(n),data,"bad"+str(n))
    def test_result_limit_and_no_long_unbounded_excerpt(self):
        self.add("doc",(b"mavi\n"*100)+b"mavi"+b"x"*600,"a")
        self.assertEqual(len(self.i.search("mavi")),24)
