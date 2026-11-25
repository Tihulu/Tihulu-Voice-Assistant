import asyncio
import unittest
from io import BytesIO
from unittest.mock import patch
import wave
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from inventory import Inventory,Entry
from inference import Candidate
from voice_pipeline import plan_voice
from voice_turn import VoiceTurn,reason_target,speech_target
from voice_review import target
from test_voice_gate import LOUD,QUIET
from test_jan_replay import frame,event

class VoiceTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.i=Inventory(10)
        self.c=tuple(Candidate(i,t,"local",p,True,True,m,80) for i,t,p,m in (
            ("stt","stt","whisper",2),("jan","llm","jan",4),("tts","tts","piper",1)))
        for n,c in enumerate(self.c):
            self.i.register(Entry(c.identity,str(n+1)*64,c.task,c.provider,True,False))
        self.plan=plan_voice(self.ctx,self.c,10)
        self.v=VoiceTurn(self.g,self.ctx,self.i,self.plan,self.c,"turn")
        self.addCleanup(self.v.close)
        self.pcm=LOUD*3
        self.url="http://127.0.0.1:1337/v1"
        self.response=frame([event("Guten Tag"),b"[DONE]"])
    def grant(self,name,t):
        return self.g.fixture_grant(name,Request(self.ctx,"engine",t,"local"),60)
    def draft(self):
        self.sg=self.grant("stt",target(self.c[0],"turn",self.pcm))
        return self.v.transcribe(self.pcm,self.sg,(("text","Merhaba"),("completed",None)))
    def llm_grant(self,text="Hallo"):
        return self.grant("llm",reason_target(self.c[1],"turn",self.url,text))
    async def answer(self):
        d=self.draft()
        return await self.v.reason(d,"Hallo",self.llm_grant(),self.url,self.response,confirmed=True)
    async def test_multilingual_review_edit_reason_speech_and_release(self):
        self.assertEqual(self.i.capacity_snapshot(),(10,7,3))
        self.assertEqual(await self.answer(),"Guten Tag")
        grant=self.grant("tts",speech_target(self.c[2],"turn","Guten Tag"))
        with self.assertRaises(Refused):
            self.v.speech(grant,QUIET)
        raw=self.v.speech(grant,QUIET,confirmed=True)
        with wave.open(BytesIO(raw),"rb") as w:
            self.assertEqual(w.readframes(320),QUIET)
        self.assertEqual(self.v.state,"completed")
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
        with self.assertRaises(Refused):
            self.v.speech(grant,QUIET,confirmed=True)
    async def test_no_auto_submit_and_stale_draft_refused(self):
        d=self.draft()
        grant=self.llm_grant()
        for draft,confirmed in ((d,False),(replace(d),True)):
            with self.assertRaises(Refused):
                await self.v.reason(draft,"Hallo",grant,self.url,self.response,confirmed=confirmed)
        self.assertEqual(self.v.state,"review")
        self.assertEqual(await self.v.reason(d,"Hallo",grant,self.url,self.response,confirmed=True),"Guten Tag")
    async def test_edited_text_must_match_exact_grant(self):
        d=self.draft()
        with self.assertRaises(Refused):
            await self.v.reason(d,"different",self.llm_grant(),self.url,self.response,confirmed=True)
        self.assertEqual(self.v.state,"held")
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
    async def test_revoked_transcript_invalidates_later_stages(self):
        d=self.draft()
        self.g.revoke("stt")
        self.assertEqual(self.v.poll(),"held")
        with self.assertRaises(Refused):
            await self.v.reason(d,"Hallo",self.llm_grant(),self.url,self.response,confirmed=True)
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
    async def test_close_while_reasoning_cannot_publish_answer(self):
        d=self.draft()
        entered,resume=asyncio.Event(),asyncio.Event()
        async def blocked(*args):
            entered.set()
            await resume.wait()
            return b"late"
        with patch("voice_turn.replay",side_effect=blocked):
            task=asyncio.create_task(self.v.reason(d,"Hallo",self.llm_grant(),self.url,self.response,confirmed=True))
            try:
                await asyncio.wait_for(entered.wait(),2)
                self.v.close()
            finally:
                resume.set()
            with self.assertRaises(Refused):
                await asyncio.wait_for(task,2)
        self.assertEqual(self.v.state,"closed")
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
    async def test_revocation_after_answer_refuses_speech(self):
        await self.answer()
        self.g.revoke("llm")
        grant=self.grant("tts",speech_target(self.c[2],"turn","Guten Tag"))
        with self.assertRaises(Refused):
            self.v.speech(grant,QUIET,confirmed=True)
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
    async def test_invalid_audio_or_failed_replay_releases_capacity(self):
        d=self.draft()
        with self.assertRaises(Refused):
            await self.v.reason(d,"Hallo",self.llm_grant(),self.url,b"invalid",confirmed=True)
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
    async def test_second_turn_respects_aggregate_limit(self):
        with self.assertRaisesRegex(Refused,"aggregate_capacity_exceeded"):
            VoiceTurn(self.g,self.ctx,self.i,self.plan,self.c,"second")
        self.v.close()
        second=VoiceTurn(self.g,self.ctx,self.i,self.plan,self.c,"second")
        second.close()
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))
