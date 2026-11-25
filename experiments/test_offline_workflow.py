import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Candidate
from offline_knowledge import OfflineIndex,document_target
from offline_workflow import plan,execute
from test_jan_replay import frame,event

class OfflineWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.index=OfflineIndex(self.g,self.ctx)
        data="Mavi renk\nGrüße".encode()
        self.doc=self.g.fixture_grant("doc",Request(self.ctx,"engine",document_target("note",data),"local","private"),60)
        self.index.add("note",data,self.doc)
        self.c=Candidate("jan","llm","local","jan",True,True,4,80)
        self.url="http://127.0.0.1:1337/v1"
        self.p=plan(self.index,"mavi",self.c,self.url,"job")
        self.call=self.g.fixture_grant("call",Request(self.ctx,"engine",self.p.request_target,"local"),60)
    async def run_fixture(self):
        return await execute(self.index,"mavi",self.c,self.url,"job",self.p,self.g,self.ctx,self.call,
                             frame([event("Mavi renk"),b"[DONE]"]))
    async def test_local_evidence_to_jan_wire_replay(self):
        self.assertEqual(await self.run_fixture(),b"Mavi renk")
        with self.assertRaises(Refused):
            await self.run_fixture()
    async def test_revoked_evidence_prevents_call_and_late_result(self):
        from jan_replay import replay
        async def revoked(*args,**kwargs):
            out=await replay(*args,**kwargs)
            self.g.revoke("doc")
            return out
        with patch("offline_workflow.replay",side_effect=revoked):
            with self.assertRaises(Refused):
                await self.run_fixture()
        with patch("offline_workflow.replay") as call:
            with self.assertRaises(Refused):
                await self.run_fixture()
            call.assert_not_called()
