import asyncio
import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Candidate
from conversation import session_target
from rich_chat import room_target,source_target,publish_target,export_target
from voice_review import target
from workspace import Workspace,identities,workspace_target,attachment_target
from test_jan_replay import frame,event
from test_voice_gate import LOUD
from test_rich_chat import CSV

class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY",False,True)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),profiles.Rule("publish_artifact",(),"allow"),profiles.Rule("host_shell",(),"allow"),profiles.Rule("external_network",(),"allow")),clock=profiles.PolicyClock())
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
        self.room=self.s.result_scope()
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

    async def test_explicit_local_attachment_exact_part_and_question(self):
        c,_=self.card("file","text",b"PRIVATE-CANARY")
        req=attachment_target(self.s.scope,c,"preview","Summarize")
        g=self.grant("engine",req,"private")
        with self.assertRaises(Refused):
            self.s.attach(c.identity,"preview","Summarize",g)
        with self.assertRaises(Refused):
            self.s.attach(c.identity,"preview","Different",g,confirmed=True)
        d=self.s.attach(c.identity,"preview","Summarize",g,confirmed=True)
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(d,"summary")
            turns=replay.call_args.args[6]
            self.assertIn("PRIVATE-CANARY",turns[-1].text)
            self.assertIn("untrusted_text",turns[-1].text)
        self.assertEqual([i.kind for i in self.s.timeline()],["card","user","assistant"])

    async def test_attachment_revocation_before_send_clears_draft(self):
        c,sg=self.card("file","text",b"private")
        g=self.grant("engine",attachment_target(self.s.scope,c,"preview","Read"),"private")
        d=self.s.attach(c.identity,"preview","Read",g,confirmed=True)
        self.g.revoke(sg.identity)
        self.assertEqual(self.s.poll(),"held")
        with self.assertRaises(Refused):
            await self.send(d,"late")
        self.assertEqual(self.s._chat._history,())

    async def test_used_attachment_clear_invalidates_derived_history(self):
        c,_=self.card("file","text",b"private")
        g=self.grant("engine",attachment_target(self.s.scope,c,"preview","Read"),"private")
        await self.send(self.s.attach(c.identity,"preview","Read",g,confirmed=True),"derived")
        with self.assertRaises(Refused):
            self.s.clear_card(c.identity)
        self.assertEqual(self.s.state,"held")
        self.assertEqual(self.s._timeline,[])
        self.assertEqual(self.s._chat._history,())

    async def test_attachment_cannot_include_svg_or_oversized_file(self):
        c,_=self.card()
        with self.assertRaises(Refused):
            attachment_target(self.s.scope,c,"chart","Read")
        f,_=self.card("big","text",b"x"*4097)
        with self.assertRaises(Refused):
            attachment_target(self.s.scope,f,"preview","Read")

    async def test_discarded_attachment_is_not_in_next_request(self):
        c,_=self.card("file","text",b"PRIVATE-CANARY")
        g=self.grant("engine",attachment_target(self.s.scope,c,"preview","Read"),"private")
        self.s.attach(c.identity,"preview","Read",g,confirmed=True)
        self.s.discard_draft()
        with self.assertRaises(Refused):
            self.g.checkpoint(g,g.request)
        d=self.s.draft("ordinary")
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(d,"ok")
            self.assertNotIn("PRIVATE-CANARY",repr(replay.call_args.args[6]))

    def enable_board(self):
        from task_board import board_scope,board_target
        room=self.s.task_room()
        root=self.grant("agent",board_target(room,2,10))
        self.s.enable_tasks(2,10,root,confirmed=True)
        return root,board_scope(room,root.identity)
    def queue_task(self,scope,identity="job",payload=b"PRIVATE-TASK"):
        from task_board import plan
        job,spec=plan(scope,identity,"opencode","plan",payload,4,50)
        sg=self.grant("agent",job.target())
        cg=self.grant("agent",spec.target())
        self.s.queue_task(identity,"opencode","plan",payload,4,50,sg,cg,confirmed=True)
        return sg,cg
    def task_frame(self,scope,identity,sequence,kind):
        import json
        from task_board import scoped
        return json.dumps(dict(session=scope,job=scoped(scope,identity),sequence=sequence,kind=kind)).encode()
    async def test_task_timeline_status_and_chat_context_isolation(self):
        _,scope=self.enable_board()
        self.queue_task(scope)
        self.s.dispatch_tasks()
        self.s.task_event("job",self.task_frame(scope,"job",1,"progress"))
        d=self.s.draft("hello")
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(d,"hi")
            self.assertNotIn("PRIVATE-TASK",repr(replay.call_args.args[6]))
        self.s.task_event("job",self.task_frame(scope,"job",2,"completed"))
        items=self.s.timeline()
        self.assertEqual([i.kind for i in items],["task","user","assistant"])
        self.assertEqual(items[0].value.state,"completed")
        self.assertEqual(self.s.task_capacity(),(0,0,0))
    async def test_task_cancel_while_chat_is_running(self):
        _,scope=self.enable_board()
        self.queue_task(scope)
        self.s.dispatch_tasks()
        d=self.s.draft("wait")
        g=self.grant("engine",d.target)
        entered,resume=asyncio.Event(),asyncio.Event()
        async def blocked(*args):
            entered.set()
            await resume.wait()
            return b"answer"
        with patch("conversation.replay",side_effect=blocked):
            task=asyncio.create_task(self.s.submit(d,g,b"fixture",confirmed=True))
            try:
                await asyncio.wait_for(entered.wait(),2)
                self.assertEqual(self.s.cancel_task("job").state,"cancelled")
                self.assertEqual(self.s.task_capacity(),(0,0,0))
            finally:
                resume.set()
            self.assertEqual(await asyncio.wait_for(task,2),"answer")
        self.assertEqual(self.s.timeline()[0].value.state,"cancelled")
    async def test_board_revocation_closes_workspace_and_drains_tasks(self):
        root,scope=self.enable_board()
        grants=self.queue_task(scope)
        self.s.dispatch_tasks()
        self.g.revoke(root.identity)
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._tasks._scheduler.poll(),(0,0,0))
        for grant in grants:
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)
    async def test_workspace_close_drains_queued_and_running_tasks(self):
        _,scope=self.enable_board()
        grants=self.queue_task(scope)
        self.s.dispatch_tasks()
        more=self.queue_task(scope,"other")
        self.s.close()
        self.assertEqual(self.s._tasks._scheduler.poll(),(0,0,0))
        for grant in (*grants,*more):
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)
    async def test_explicit_board_and_task_confirmation(self):
        from task_board import board_target,plan
        root=self.grant("agent",board_target(self.s.task_room(),2,10))
        with self.assertRaises(Refused):
            self.s.enable_tasks(2,10,root)
        self.assertIsNone(self.s._tasks)
        self.s.enable_tasks(2,10,root,confirmed=True)
        scope=self.s._tasks._room
        job,spec=plan(scope,"job","opencode","plan",b"x",4,50)
        sg=self.grant("agent",job.target())
        cg=self.grant("agent",spec.target())
        with self.assertRaises(Refused):
            self.s.queue_task("job","opencode","plan",b"x",4,50,sg,cg)
        self.assertEqual(self.s.timeline(),())

    def completed_analysis(self,scope):
        from task_board import plan
        job,spec=plan(scope,"analysis","opencode","local-analysis",CSV,4,50)
        sg=self.grant("agent",job.target())
        cg=self.grant("agent",spec.target())
        self.s.queue_task("analysis","opencode","local-analysis",CSV,4,50,sg,cg,confirmed=True)
        self.s.dispatch_tasks()
        self.s.task_event("analysis",self.task_frame(scope,"analysis",1,"completed"))
        return sg,cg
    def linked_card(self):
        from workspace import task_result_target
        receipt=self.s._tasks.receipt("analysis")
        sg=self.grant("engine",source_target("result","temperature",CSV,room=self.room),"private")
        lg=self.grant("engine",task_result_target(receipt,"result",CSV),"private")
        c=self.s.prepare_task_card("analysis","result",CSV,sg,lg,confirmed=True)
        pg=self.grant("publish",publish_target(self.room,c),"private")
        self.s.publish_card(c,pg,confirmed=True)
        return c,lg
    async def test_completed_analysis_yields_linked_deterministic_card(self):
        _,scope=self.enable_board()
        self.completed_analysis(scope)
        c,lg=self.linked_card()
        items=self.s.timeline()
        self.assertEqual([i.kind for i in items],["task","card"])
        self.assertEqual(items[1].value,c)
        self.assertEqual(c.kind,"temperature")
        self.s.clear_card("result")
        with self.assertRaises(Refused):
            self.g.checkpoint(lg,lg.request)
        self.assertEqual([i.kind for i in self.s.timeline()],["task"])
    async def test_task_grant_revocation_invalidates_linked_result(self):
        _,scope=self.enable_board()
        sg,cg=self.completed_analysis(scope)
        c,lg=self.linked_card()
        self.g.revoke(sg.identity)
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._rich._cards,{})
        with self.assertRaises(Refused):
            self.g.checkpoint(lg,lg.request)
    async def test_task_result_rejects_changed_input_and_missing_confirmation(self):
        from workspace import task_result_target
        _,scope=self.enable_board()
        self.completed_analysis(scope)
        receipt=self.s._tasks.receipt("analysis")
        sg=self.grant("engine",source_target("result","temperature",CSV,room=self.room),"private")
        lg=self.grant("engine",task_result_target(receipt,"result",CSV),"private")
        with self.assertRaises(Refused):
            self.s.prepare_task_card("analysis","result",CSV,sg,lg)
        with self.assertRaises(Refused):
            self.s.prepare_task_card("analysis","result",CSV+b"\n",sg,lg,confirmed=True)
        c=self.s.prepare_task_card("analysis","result",CSV,sg,lg,confirmed=True)
        self.s.discard_card()
        with self.assertRaises(Refused):
            self.g.checkpoint(lg,lg.request)

    def research_setup(self):
        from research_board import board_target,board_scope
        root="https://example.com/docs"
        room=self.s.research_room()
        grant=self.grant("research",board_target(room,root),"public")
        self.s.enable_research(root,grant,confirmed=True)
        return grant,board_scope(room,grant.identity)
    def research_card(self,scope,identity="report",raw=b"PUBLIC-CANARY"):
        from research import canonical
        from research_report import Span
        from research_board import collect_target,publication_target
        from hashlib import sha256
        url="https://example.com/docs/"+identity
        n=self.grant("research",canonical(url).identity,"public")
        s=self.grant("research",collect_target(scope,url,raw),"public")
        i=self.s.collect_source(url,("8.8.8.8",),"8.8.8.8",
             (("Content-Type","text/plain; charset=utf-8"),("Content-Length",str(len(raw)))),raw,n,s,confirmed=True)
        c=self.s.prepare_research(identity,(Span(i,sha256(raw).hexdigest(),0,len(raw.decode())),))
        p=self.grant("publish",publication_target(scope,c))
        self.s.publish_research(c,p,confirmed=True)
        return c,n,s,p
    async def test_research_card_timeline_without_automatic_model_context(self):
        _,scope=self.research_setup()
        c,_,_,_=self.research_card(scope)
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(self.s.draft("hello"),"answer")
            self.assertNotIn("PUBLIC-CANARY",repr(replay.call_args.args[6]))
        self.assertEqual([i.kind for i in self.s.timeline()],["research","user","assistant"])
        self.assertEqual(self.s.timeline()[0].value,c)
    async def test_research_attachment_explicit_and_revocable(self):
        from workspace import research_attachment_target
        _,scope=self.research_setup()
        c,n,s,p=self.research_card(scope)
        g=self.grant("engine",research_attachment_target(scope,c,"Summarize"),"private")
        with self.assertRaises(Refused):
            self.s.attach_research(c.identity,"Summarize",g)
        d=self.s.attach_research(c.identity,"Summarize",g,confirmed=True)
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(d,"answer")
            self.assertIn("PUBLIC-CANARY",repr(replay.call_args.args[6]))
            self.assertIn("untrusted_research",repr(replay.call_args.args[6]))
        self.g.revoke(s.identity)
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._chat._history,())
    async def test_unused_research_revocation_only_removes_card(self):
        _,scope=self.research_setup()
        c,n,s,p=self.research_card(scope)
        await self.send(self.s.draft("hello"),"answer")
        self.g.revoke(n.identity)
        self.assertEqual([i.kind for i in self.s.timeline()],["user","assistant"])
        self.assertEqual(self.s.state,"ready")
    async def test_research_exact_export_and_close(self):
        from research_board import export_target
        root,scope=self.research_setup()
        c,n,s,p=self.research_card(scope)
        g=self.grant("publish",export_target(scope,c,"slot"))
        with self.assertRaises(Refused):
            self.s.export_research(c.identity,"other",g,confirmed=True)
        self.assertEqual(self.s.export_research(c.identity,"slot",g,confirmed=True),c.payload)
        self.s.close()
        for grant in (root,n,s,p):
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)
    async def test_replaced_research_draft_drops_consent(self):
        from workspace import research_attachment_target
        _,scope=self.research_setup()
        c,n,s,p=self.research_card(scope)
        g=self.grant("engine",research_attachment_target(scope,c,"Read"),"private")
        self.s.attach_research(c.identity,"Read",g,confirmed=True)
        d=self.s.draft("new")
        with self.assertRaises(Refused):
            self.g.checkpoint(g,g.request)
        with patch("conversation.replay",wraps=__import__("conversation").replay) as replay:
            await self.send(d,"ok")
            self.assertNotIn("PUBLIC-CANARY",repr(replay.call_args.args[6]))

    async def test_same_named_workspace_voice_and_attachment_targets_differ(self):
        chat,room=identities("workspace")
        root=self.grant("engine",workspace_target("workspace",self.c,self.url))
        cg=self.grant("engine",session_target(self.c,self.url,chat))
        rg=self.grant("publish",room_target(room))
        other=Workspace(self.g,self.ctx,"workspace",self.c,self.url,root,cg,rg)
        self.addCleanup(other.close)
        self.assertNotEqual(self.s.voice_identity(),other.voice_identity())
        self.assertNotEqual(self.s.result_scope(),other.result_scope())
        card,_=self.card("file","text",b"private")
        self.assertNotEqual(attachment_target(self.s.scope,card,"preview","Read"),
                            attachment_target(other.scope,card,"preview","Read"))

    async def test_revoked_unpublished_task_card_does_not_block_new_draft(self):
        from workspace import task_result_target
        _,scope=self.enable_board()
        self.completed_analysis(scope)
        receipt=self.s._tasks.receipt("analysis")
        sg=self.grant("engine",source_target("result","temperature",CSV,room=self.room),"private")
        lg=self.grant("engine",task_result_target(receipt,"result",CSV),"private")
        self.s.prepare_task_card("analysis","result",CSV,sg,lg,confirmed=True)
        self.g.revoke(sg.identity)
        self.assertEqual(self.s.poll(),"ready")
        self.assertIsNone(self.s._task_draft)
        with self.assertRaises(Refused):
            self.g.checkpoint(lg,lg.request)
        self.card("fresh","text",b"fresh")

    async def test_attachment_revoked_after_adapter_claim_prevents_first_delta(self):
        card,source=self.card("file","text",b"PRIVATE-CANARY")
        ag=self.grant("engine",attachment_target(self.s.scope,card,"preview","Read"),"private")
        d=self.s.attach(card.identity,"preview","Read",ag,confirmed=True)
        g=self.grant("engine",d.target)
        original=self.g.claim
        def revoke(grant,req):
            original(grant,req)
            if grant is g:
                self.g.revoke(source.identity)
        with patch.object(self.g,"claim",side_effect=revoke), patch("jan_replay.LocalAdapter.event",autospec=True) as event_call:
            with self.assertRaises(Refused):
                await self.s.submit(d,g,frame([event("answer"),b"[DONE]"]),confirmed=True)
            event_call.assert_not_called()
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._chat._history,())

    async def test_root_revoked_between_deltas_blocks_next_delta(self):
        from adapter_protocol import LocalAdapter
        d=self.s.draft("hello")
        g=self.grant("engine",d.target)
        original=LocalAdapter.event
        seen=[]
        def revoke(adapter,identity,sequence,kind,value):
            seen.append(sequence)
            original(adapter,identity,sequence,kind,value)
            self.g.revoke(self.root.identity)
        with patch.object(LocalAdapter,"event",new=revoke):
            with self.assertRaises(Refused):
                await self.s.submit(d,g,frame([event("one"),event("two"),b"[DONE]"]),confirmed=True)
        self.assertEqual(seen,[1])
        self.assertEqual(self.s.poll(),"held")
        self.assertEqual(self.s._timeline,[])

    async def test_research_revoked_before_replay_claim_refuses_adapter(self):
        _,scope=self.research_setup()
        card,n,s,p=self.research_card(scope)
        from workspace import research_attachment_target
        ag=self.grant("engine",research_attachment_target(scope,card,"Read"),"private")
        d=self.s.attach_research(card.identity,"Read",ag,confirmed=True)
        g=self.grant("engine",d.target)
        original=__import__("jan_replay").replay
        async def revoke(*args):
            self.g.revoke(n.identity)
            return await original(*args)
        with patch("conversation.replay",side_effect=revoke),patch("jan_replay.LocalAdapter") as adapter:
            with self.assertRaises(Refused):
                await self.s.submit(d,g,frame([event("answer"),b"[DONE]"]),confirmed=True)
            adapter.assert_not_called()
        self.assertEqual(self.s.poll(),"held")
