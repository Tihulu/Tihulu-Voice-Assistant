import asyncio
import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Request,Refused
from inference import Candidate
from conversation import session_target
from rich_chat import room_target
from workspace import Workspace,identities,workspace_target
from presenter import Presenter
from test_jan_replay import frame,event

class PresenterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),profiles.Rule("publish_artifact",(),"allow")),clock=profiles.PolicyClock())
        self.c=Candidate("jan","llm","local","jan",True,True,4,80)
        self.url="http://127.0.0.1:1337/v1"
        self.n=0
        self.p=Presenter()
        self.addCleanup(self.p.close)
        self.w=self.workspace("room")
        self.epoch=self.p.bind(self.w,confirmed=True)
    def grant(self,action,target):
        self.n+=1
        return self.g.fixture_grant("g"+str(self.n),Request(self.ctx,action,target,"local"),60)
    def workspace(self,name):
        chat,room=identities(name)
        return Workspace(self.g,self.ctx,name,self.c,self.url,
                         self.grant("engine",workspace_target(name,self.c,self.url)),
                         self.grant("engine",session_target(self.c,self.url,chat)),
                         self.grant("publish",room_target(room)))
    async def send(self,d):
        return await self.p.submit(self.epoch,d,self.grant("engine",d.target),frame([event("answer"),b"[DONE]"]),confirmed=True)
    async def test_view_and_explicit_submit(self):
        self.assertTrue(self.p.view().can_draft)
        d=self.p.draft(self.epoch,"hello")
        self.assertEqual(self.p.view().items,())
        g=self.grant("engine",d.target)
        with self.assertRaises(Refused):
            await self.p.submit(self.epoch,d,g,frame([event("answer"),b"[DONE]"]))
        await self.p.submit(self.epoch,d,g,frame([event("answer"),b"[DONE]"]),confirmed=True)
        v=self.p.view()
        self.assertEqual([i.kind for i in v.items],["user","assistant"])
        self.assertEqual(v.revision,2)
    async def test_switch_rejects_old_epoch_and_keeps_new_workspace(self):
        d=self.p.draft(self.epoch,"private")
        await self.send(d)
        second=self.workspace("room")
        epoch=self.p.bind(second,confirmed=True)
        self.assertEqual(self.w.state,"closed")
        self.assertEqual(self.p.view().items,())
        for fn in (lambda:self.p.draft(self.epoch,"stale"),lambda:self.p.stop(self.epoch)):
            with self.assertRaises(Refused):
                fn()
        self.assertEqual(self.p.view().epoch,epoch)
        self.assertEqual(second.state,"ready")
    async def test_switch_during_reply_rejects_late_callback(self):
        d=self.p.draft(self.epoch,"wait")
        entered,resume=asyncio.Event(),asyncio.Event()
        async def blocked(*args):
            entered.set()
            await resume.wait()
            return b"late"
        with patch("conversation.replay",side_effect=blocked):
            task=asyncio.create_task(self.send(d))
            try:
                await asyncio.wait_for(entered.wait(),2)
                self.assertFalse(self.p.view().can_draft)
                self.assertTrue(self.p.view().can_stop)
                second=self.workspace("room")
                epoch=self.p.bind(second,confirmed=True)
            finally:
                resume.set()
            with self.assertRaises(Refused):
                await asyncio.wait_for(task,2)
        self.assertEqual(self.p.view().epoch,epoch)
        self.assertEqual(self.p.view().items,())
    async def test_stop_clears_current_view_and_refuses_old_actions(self):
        await self.send(self.p.draft(self.epoch,"hello"))
        self.p.stop(self.epoch)
        self.assertEqual(self.p.view().state,"idle")
        self.assertEqual(self.p.view().items,())
        self.assertEqual(self.w.state,"closed")
        with self.assertRaises(Refused):
            self.p.draft(self.epoch,"late")
    async def test_revoked_workspace_never_returns_cached_timeline(self):
        await self.send(self.p.draft(self.epoch,"hello"))
        self.g.close()
        v=self.p.view()
        self.assertEqual(v.state,"held")
        self.assertEqual(v.items,())
        self.assertFalse(v.can_stop)
    async def test_discarded_draft_and_foreign_epoch_refused(self):
        d=self.p.draft(self.epoch,"draft")
        self.p.discard(self.epoch)
        with self.assertRaises(Refused):
            await self.send(d)
        with self.assertRaises(Refused):
            self.p.draft(True,"bool epoch")
        self.assertEqual(self.p.view().items,())
