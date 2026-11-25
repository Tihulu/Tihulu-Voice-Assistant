import unittest
import json
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from research import canonical
from research_session import ResearchSession
from research_report import Span,ReportBuilder
class ResearchReportTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY",False,True)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("external_network",(),"allow"),profiles.Rule("publish_artifact",(),"allow")),clock=profiles.PolicyClock())
        self.url="https://example.com/docs/a"
        self.data="Mavi <script> örnek".encode()
        self.identity=canonical(self.url).identity
        self.grant=self.g.fixture_grant("r",Request(self.ctx,"research",self.identity,"local","public"),60)
        self.s=ResearchSession(self.g,self.ctx,"https://example.com/docs")
        self.s.collect(self.grant,self.url,("8.8.8.8",),"8.8.8.8",
                       (("Content-Type","text/plain; charset=utf-8"),("Content-Length",str(len(self.data)))),self.data)
        self.span=Span(self.identity,sha256(self.data).hexdigest(),0,4)
        self.b=ReportBuilder(self.g,self.ctx,self.s,(self.span,))
    def test_structured_citation_and_exact_export(self):
        report=self.b.preview()
        self.assertEqual(json.loads(report.payload)["sources"][0]["quote"],"Mavi")
        self.assertEqual(sha256(report.payload).hexdigest(),report.digest)
        g=self.g.fixture_grant("p",Request(self.ctx,"publish",report.digest,"local"),60)
        self.assertEqual(self.b.export_bytes(g,report.digest),report.payload)
        with self.assertRaises(Refused):
            self.b.export_bytes(g,report.digest)
    def test_revoked_source_prevents_report_and_export(self):
        report=self.b.preview()
        g=self.g.fixture_grant("p",Request(self.ctx,"publish",report.digest,"local"),60)
        self.g.revoke("r")
        with self.assertRaises(Refused):
            self.b.export_bytes(g,report.digest)
        with self.assertRaises(Refused):
            self.b.preview()
    def test_invalid_spans_and_stale_digest(self):
        b=ReportBuilder(self.g,self.ctx,self.s,(Span(self.identity,"a"*64,0,4),))
        with self.assertRaises(Refused):
            b.preview()
        report=self.b.preview()
        g=self.g.fixture_grant("p",Request(self.ctx,"publish",report.digest,"local"),60)
        with self.assertRaises(Refused):
            self.b.export_bytes(g,"b"*64)
        self.b.close()
        with self.assertRaises(Refused):
            self.b.preview()
