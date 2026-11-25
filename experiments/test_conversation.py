import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Candidate
from jan_replay import Turn
from conversation import Conversation,MAX_PAIRS,session_target
from test_jan_replay import frame,event

class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.c=Candidate("jan","llm","local","jan",True,True,4,80)
        self.url="http://127.0.0.1:1337/v1"
        self.session_req=Request(self.ctx,"engine",session_target(self.c,self.url,"chat"),"local")
        self.session_grant=self.g.fixture_grant("session",self.session_req,60)
        self.s=Conversation(self.g,self.ctx,self.c,self.url,"chat",self.session_grant)
        self.addCleanup(self.s.close)
        self.n=0
    def grant(self,d):
        self.n+=1
        return self.g.fixture_grant("g"+str(self.n),Request(self.ctx,"engine",d.target,"local"),60)
    async def send(self,text,answer):
        d=self.s.draft(text)
        g=self.grant(d)
        self.assertEqual(await self.s.submit(d,g,frame([event(answer),b"[DONE]"]),confirmed=True),answer)
        return g
    async def test_four_turn_history_and_multilingual_followups(self):
        pairs=(("Rengim mavi","Not aldım"),("Welche Farbe?","Blau"),("In English?","Blue"),("Hatırlıyor musun?","Mavi"))
        for q,a in pairs:
            await self.send(q,a)
        self.assertEqual(self.s.history(),tuple(Turn(role,text) for q,a in pairs for role,text in (("user",q),("assistant",a))))
        self.assertEqual(self.s.state,"ready")
    async def test_edit_requires_new_exact_grant_and_current_draft(self):
        d=self.s.draft("first")
        old=self.grant(d)
        current=self.s.draft("edited")
        for draft,grant,confirm in ((d,old,True),(replace(current),old,True),(current,old,True),(current,self.grant(current),False)):
            with self.assertRaises(Refused):
                await self.s.submit(draft,grant,frame([event("answer"),b"[DONE]"]),confirmed=confirm)
        self.assertEqual(self.s.history(),())
        self.assertEqual(self.s.state,"review")
        await self.s.submit(current,self.grant(current),frame([event("answer"),b"[DONE]"]),confirmed=True)
    async def test_identical_text_has_fresh_target_with_history(self):
        d=self.s.draft("same")
        await self.s.submit(d,self.grant(d),frame([event("answer"),b"[DONE]"]),confirmed=True)
        d2=self.s.draft("same")
        self.assertNotEqual(d.target,d2.target)
        self.s.discard_draft()
        d3=self.s.draft("same")
        self.assertNotEqual(d2.target,d3.target)
    async def test_previous_turn_revocation_invalidates_entire_history(self):
        old=await self.send("mavi","noted")
        await self.send("which","blue")
        self.g.revoke(old.identity)
        self.assertEqual(self.s.poll(),"held")
        for action in (self.s.history,lambda:self.s.draft("next")):
            with self.assertRaises(Refused):
                action()
    async def test_profile_close_and_stale_route(self):
        await self.send("private","local")
        with self.assertRaises(Refused):
            Conversation(self.g,replace(self.ctx,route="other"),self.c,self.url,"other",self.session_grant)
        self.g.close()
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._history,())
    async def test_close_during_replay_refuses_late_result_and_duplicate_submit(self):
        d=self.s.draft("test")
        grant=self.grant(d)
        entered,resume=asyncio.Event(),asyncio.Event()
        async def blocked(*args):
            entered.set()
            await resume.wait()
            return b"late"
        with patch("conversation.replay",side_effect=blocked):
            task=asyncio.create_task(self.s.submit(d,grant,b"fixture",confirmed=True))
            try:
                await asyncio.wait_for(entered.wait(),2)
                with self.assertRaises(Refused):
                    self.s.draft("second")
                with self.assertRaises(Refused):
                    await self.s.submit(d,grant,b"fixture",confirmed=True)
                self.s.close()
            finally:
                resume.set()
            with self.assertRaises(Refused):
                await asyncio.wait_for(task,2)
        self.assertEqual(self.s.state,"closed")
        self.assertEqual(self.s._history,())
    async def test_failed_stream_clears_retained_history(self):
        await self.send("first","answer")
        d=self.s.draft("second")
        with self.assertRaises(Refused):
            await self.s.submit(d,self.grant(d),b"invalid",confirmed=True)
        self.assertEqual(self.s.state,"held")
        self.assertEqual(self.s._history,())
    async def test_bounded_history_refuses_without_silent_truncation(self):
        for _ in range(MAX_PAIRS):
            await self.send("question","answer")
        with self.assertRaisesRegex(Refused,"conversation_limit"):
            self.s.draft("extra")
        self.assertEqual(len(self.s.history()),MAX_PAIRS*2)
    async def test_discard_does_not_erase_predecessor(self):
        await self.send("first","answer")
        self.s.draft("unsent")
        self.s.discard_draft()
        self.assertEqual(self.s.history(),(Turn("user","first"),Turn("assistant","answer")))

    async def test_empty_session_cannot_survive_profile_close(self):
        self.s.draft("unsent")
        self.g.close()
        self.assertEqual(self.s.poll(),"held")
        self.assertIsNone(self.s._draft)
        with self.assertRaises(Refused):
            self.s.draft("after switch")

    async def test_session_grant_cannot_be_replayed_or_redirected(self):
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            Conversation(self.g,self.ctx,self.c,self.url,"chat",self.session_grant)
        for url,identity in ((self.url,"other"),("http://127.0.0.1:1338/v1","chat")):
            with self.assertRaises(Refused):
                Conversation(self.g,self.ctx,self.c,url,identity,self.session_grant)
        await self.send("still owned","yes")

    async def test_session_revocation_clears_unsent_draft_and_history(self):
        await self.send("first","answer")
        self.s.draft("unsent")
        self.g.revoke("session")
        self.assertEqual(self.s.poll(),"held")
        self.assertIsNone(self.s._draft)
        self.assertEqual(self.s._history,())

    async def test_same_named_conversation_cannot_reuse_unclaimed_turn_grant(self):
        root=self.g.fixture_grant("second",self.session_req,60)
        other=Conversation(self.g,self.ctx,self.c,self.url,"chat",root)
        self.addCleanup(other.close)
        original=self.s.draft("same")
        g=self.grant(original)
        current=other.draft("same")
        self.assertNotEqual(original.target,current.target)
        with self.assertRaises(Refused):
            await other.submit(current,g,frame([event("wrong"),b"[DONE]"]),confirmed=True)
        self.assertEqual(other.history(),())
        await self.s.submit(original,g,frame([event("right"),b"[DONE]"]),confirmed=True)
