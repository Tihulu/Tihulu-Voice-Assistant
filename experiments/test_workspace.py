import asyncio
import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Candidate
from conversation import session_target
from rich_chat import room_target,source_target,publish_target,export_target
from voice_review import target
from workspace import Workspace,identities,workspace_target
from test_jan_replay import frame,event
from test_voice_gate import LOUD
from test_rich_chat import CSV

class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),profiles.Rule("publish_artifact",(),"allow")),clock=profiles.PolicyClock())
        self.c=Candidate("jan","llm","local","jan",True,True,4,80)
        self.stt=Candidate("whisper","stt","local","whisper",True,True,2,80)
        self.url="http://127.0.0.1:1337/v1"
        self.n=0
        self.chat,self.room=identities("workspace")
        self.root=self.grant("engine",workspace_target("workspace",self.c,self.url))
        self.cg=self.grant("engine",session_target(self.c,self.url,self.chat))
        self.rg=self.grant("publish",room_target(self.room))
        self.s=Workspace(self.g,self.ctx,"workspace",self.c,self.url,self.root,self.cg,self.rg)
        self.addCleanup(self.s.close)
    def grant(self,action,target,classification="none"):
        self.n+=1
        return self.g.fixture_grant("g"+str(self.n),Request(self.ctx,action,target,"local",classification),60)
    async def send(self,d,answer):
        return await self.s.submit(d,self.grant("engine",d.target),frame([event(answer),b"[DONE]"]),confirmed=True)
    def card(self,identity="chart",kind="temperature",data=CSV):
        sg=self.grant("engine",source_target(identity,kind,data,room=self.room),"private")
        card=self.s.prepare_card(identity,kind,data,sg)
        pg=self.grant("publish",publish_target(self.room,card),"private")
        self.s.publish_card(card,pg,confirmed=True)
        return card,sg
    def voice(self):
        identity=self.s.voice_identity()
        pcm=LOUD*3
        sg=self.grant("engine",target(self.stt,identity,pcm))
        return self.s.voice_draft(self.stt,pcm,sg,(("text","Merhaba"),("completed",None))),sg
    async def test_ordered_text_voice_and_card_without_automatic_model_context(self):
        await self.send(self.s.draft("Hello"),"Hi")
        card,_=self.card("file","text",b"PRIVATE-CANARY")
        voice,_=self.voice()
        edited=self.s.edit_voice(voice,"Hallo")
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(edited,"Guten Tag")
            turns=replay.call_args.args[6]
            self.assertEqual([t.text for t in turns],["Hello","Hi","Hallo"])
            self.assertNotIn("PRIVATE-CANARY",repr(turns))
        items=self.s.timeline()
        self.assertEqual([i.kind for i in items],["user","assistant","card","user","assistant"])
        self.assertEqual(items[2].value,card)
        self.assertEqual([i.sequence for i in items],list(range(1,6)))
    async def test_card_revocation_prunes_only_card_and_preserves_sequence(self):
        await self.send(self.s.draft("one"),"answer")
        _,sg=self.card()
        self.g.revoke(sg.identity)
        self.assertEqual([i.sequence for i in self.s.timeline()],[1,2])
        await self.send(self.s.draft("two"),"answer")
        self.assertEqual([i.sequence for i in self.s.timeline()],[1,2,4,5])
    async def test_voice_revocation_invalidates_pending_and_all_children(self):
        self.card()
        d,sg=self.voice()
        self.g.revoke(sg.identity)
        self.assertEqual(self.s.poll(),"held")
        with self.assertRaises(Refused):
            await self.send(d,"late")
        self.assertEqual(self.s._timeline,[])
    async def test_committed_voice_revocation_invalidates_history(self):
        d,sg=self.voice()
        await self.send(d,"answer")
        self.g.revoke(sg.identity)
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._chat._history,())
    async def test_discard_and_replace_voice_draft_revoke_its_grant(self):
        d,sg=self.voice()
        self.s.discard_draft()
        with self.assertRaises(Refused):
            self.g.checkpoint(sg,sg.request)
        d,sg=self.voice()
        self.s.draft("typed")
        with self.assertRaises(Refused):
            self.g.checkpoint(sg,sg.request)
        self.assertEqual(self.s.timeline(),())
    async def test_workspace_close_during_async_reply(self):
        self.card()
        d=self.s.draft("wait")
        g=self.grant("engine",d.target)
        entered,resume=asyncio.Event(),asyncio.Event()
        async def blocked(*args):
            entered.set()
            await resume.wait()
            return b"late"
        with patch("conversation.replay",side_effect=blocked):
            task=asyncio.create_task(self.s.submit(d,g,b"fixture",confirmed=True))
            try:
                await asyncio.wait_for(entered.wait(),2)
                with self.assertRaises(Refused):
                    self.s.draft("busy")
                self.s.close()
            finally:
                resume.set()
            with self.assertRaises(Refused):
                await asyncio.wait_for(task,2)
        self.assertEqual(self.s.state,"closed")
        self.assertEqual(self.s._timeline,[])
        self.assertEqual(self.s._rich._cards,{})
    async def test_root_revocation_and_profile_close(self):
        self.card()
        self.g.revoke(self.root.identity)
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._rich._cards,{})
        with self.assertRaises(Refused):
            self.s.timeline()
    async def test_constructor_failure_revokes_only_owned_children(self):
        root=self.grant("engine",workspace_target("other",self.c,self.url))
        chat,room=identities("other")
        cg=self.grant("engine",session_target(self.c,self.url,chat))
        with self.assertRaises(Refused):
            Workspace(self.g,self.ctx,"other",self.c,self.url,root,cg,self.rg)
        for grant in (root,cg):
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)
        self.assertEqual(self.g.checkpoint(self.rg,self.rg.request),"SIMULATED_ALLOW")
    async def test_explicit_export_and_clear_card(self):
        c,_=self.card()
        g=self.grant("publish",export_target(self.room,c,"chart","slot"),"private")
        with self.assertRaises(Refused):
            self.s.export_card(c.identity,"chart","slot",g)
        self.assertEqual(self.s.export_card(c.identity,"chart","slot",g,confirmed=True),c.parts[1].data)
        self.s.clear_card(c.identity)
        self.assertEqual(self.s.timeline(),())
