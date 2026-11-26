# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from capabilities import CapabilityCall
from gateway import Context, FixtureGateway, Refused
from pdf_manipulation import PDFManipulation

class PDFManipulationTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,tuple(profiles.Rule(x,(),"allow")
            for x in ("host_shell","filesystem_read","filesystem_write")),clock=profiles.PolicyClock())
        self.split=CapabilityCall("poppler","fixture","pdf_split",
            (("artifact","source"),("digest","a"*64),("page_order",(1,2,3)),("output","parts")))
        self.parts=(b"%PDF-one",b"%PDF-two",b"%PDF-three")
        self.digests=tuple(sha256(p).hexdigest() for p in self.parts)
    def job(self,call,name,parent=None):
        return PDFManipulation(self.g,self.g.fixture_grant(name,call.request(self.ctx),60),
                               self.ctx,call,parent=parent)
    def merge(self,order=(3,1),digests=None):
        return CapabilityCall("poppler","fixture","pdf_merge",
            (("artifact","parts"),("digest","a"*64),("page_order",order),
             ("part_digests",tuple(self.digests[p-1] for p in order) if digests is None else digests),
             ("output","merged")))
    def completed_split(self):
        p=self.job(self.split,"split")
        p.complete(self.parts)
        return p
    def test_exact_split_and_ordered_merge(self):
        p=self.completed_split()
        self.assertEqual(p.result(),self.digests)
        m=self.job(self.merge(),"merge",p)
        m.complete((b"%PDF-merged",))
        self.assertEqual(m.result(),(sha256(b"%PDF-merged").hexdigest(),))
    def test_wrong_order_or_part_digest_denies(self):
        p=self.completed_split()
        for i,call in enumerate((self.merge(digests=self.digests[:2]),
                                 self.merge(order=(3,2),digests=(self.digests[2],self.digests[0])))):
            with self.assertRaisesRegex(Refused,"pdf_merge_provenance_mismatch"):
                self.job(call,"bad"+str(i),p)
    def test_missing_or_incomplete_parent_denies(self):
        with self.assertRaises(Refused):
            self.job(self.merge(),"missing")
        p=self.job(self.split,"split")
        with self.assertRaises(Refused):
            self.job(self.merge(),"incomplete",p)
    def test_parent_revocation_blocks_merge_result(self):
        p=self.completed_split()
        m=self.job(self.merge(),"merge",p)
        m.complete((b"%PDF-merged",))
        p.cancel()
        with self.assertRaises(Refused):
            m.result()
    def test_invalid_page_sets_and_output_count(self):
        for order in ((),(1,1),(True,2),(501,),list(range(2))):
            with self.assertRaises(Refused):
                replace(self.split,args=self.split.args[:2]+(("page_order",order),)+self.split.args[3:]).request(self.ctx)
        p=self.job(self.split,"split")
        with self.assertRaises(Refused):
            p.complete(self.parts[:2])
        with self.assertRaises(Refused):
            p.result()
