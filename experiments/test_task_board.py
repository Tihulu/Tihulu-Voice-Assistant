import json
import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from task_board import TaskBoard,plan,scoped,board_target,board_scope

class TaskBoardTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
        self.root=self.g.fixture_grant("root",Request(self.ctx,"agent",board_target("room",2,10),"local"),60)
        self.scope=board_scope("room","root")
        self.b=TaskBoard(self.g,self.ctx,"room",2,10,self.root)
        self.addCleanup(self.b.close)
        self.n=0
    def grants(self,i,memory=4,priority=50,payload=b"fixture"):
        job,spec=plan(self.scope,i,"opencode","plan",payload,memory,priority)
        result=[]
        for target in (job.target(),spec.target()):
            self.n+=1
            result.append(self.g.fixture_grant("g"+str(self.n),Request(self.ctx,"agent",target,"local"),60))
        return result
    def queue(self,i,memory=4,priority=50):
        g=self.grants(i,memory,priority)
        self.b.queue(i,"opencode","plan",b"fixture",memory,priority,*g)
        return g
    def event(self,i,seq,kind):
        return json.dumps(dict(session=self.scope,job=scoped(self.scope,i),sequence=seq,kind=kind)).encode()
    def test_priority_capacity_and_completion(self):
        self.queue("low",4,10)
        self.queue("high",7,90)
        self.queue("mid",3,50)
        self.assertEqual([s.identity for s in self.b.dispatch()],["high","mid"])
        self.assertEqual(self.b.capacity(),(1,2,10))
        self.assertEqual(self.b.receive("high",self.event("high",1,"progress")).sequence,1)
        self.assertEqual(self.b.receive("high",self.event("high",2,"completed")).state,"completed")
        self.assertEqual([s.identity for s in self.b.dispatch()],["low"])
        self.assertEqual(self.b.capacity(),(0,2,7))
    def test_foreign_payload_and_room_refused(self):
        g=self.grants("one")
        with self.assertRaises(Refused):
            self.b.queue("one","opencode","plan",b"changed",4,50,*g)
        root=self.g.fixture_grant("other",Request(self.ctx,"agent",board_target("room",2,10),"local"),60)
        other=TaskBoard(self.g,self.ctx,"room",2,10,root)
        self.addCleanup(other.close)
        with self.assertRaises(Refused):
            other.queue("one","opencode","plan",b"fixture",4,50,*g)
        self.b.queue("one","opencode","plan",b"fixture",4,50,*g)
    def test_revocation_queued_running_and_completed(self):
        a=self.queue("a")
        b=self.queue("b")
        self.g.revoke(a[1].identity)
        self.assertEqual([s.identity for s in self.b.dispatch()],["b"])
        self.b.receive("b",self.event("b",1,"completed"))
        self.g.revoke(b[0].identity)
        self.assertEqual([s.state for s in self.b.snapshot()],["held","held"])
        c=self.queue("c")
        self.b.dispatch()
        self.g.revoke(c[1].identity)
        self.assertEqual(self.b.capacity(),(0,0,0))
    def test_malformed_frame_cancels_and_releases_capacity(self):
        g=self.queue("one")
        self.b.dispatch()
        with self.assertRaises(Refused):
            self.b.receive("one",self.event("other",1,"completed"))
        self.assertEqual(self.b.snapshot()[0].state,"held")
        self.assertEqual(self.b.capacity(),(0,0,0))
        for grant in g:
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)
    def test_cancel_and_close_revoke_all_owned_grants(self):
        a=self.queue("a",7)
        b=self.queue("b",7)
        self.b.dispatch()
        self.b.cancel("b")
        self.b.close()
        for grant in (*a,*b):
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)
        with self.assertRaises(Refused):
            self.b.snapshot()
    def test_batch_dispatch_failure_drains_every_selected_handle(self):
        a=self.queue("a")
        b=self.queue("b")
        with patch("task_board.Connector.begin",side_effect=Refused("fixture")):
            with self.assertRaises(Refused):
                self.b.dispatch()
        self.assertEqual(self.b.capacity(),(0,0,0))
        self.assertEqual([s.state for s in self.b.snapshot()],["held","held"])
    def test_duplicate_and_late_events_refused(self):
        self.queue("one")
        self.b.dispatch()
        self.b.receive("one",self.event("one",1,"completed"))
        with self.assertRaises(Refused):
            self.b.receive("one",self.event("one",2,"progress"))
        with self.assertRaises(Refused):
            self.queue("one")

    def test_board_root_revocation_clears_all_reservations(self):
        a=self.queue("a")
        self.b.dispatch()
        self.g.revoke(self.root.identity)
        with self.assertRaises(Refused):
            self.b.snapshot()
        self.assertEqual(self.b._scheduler.poll(),(0,0,0))
        for grant in a:
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)

    def test_scope_encoding_and_root_replay_refused(self):
        self.assertNotEqual(scoped("a:b","c"),scoped("a","b:c"))
        with self.assertRaises(Refused):
            TaskBoard(self.g,self.ctx,"room",2,10,self.root)
        self.assertEqual(self.b.snapshot(),())

    def test_receipt_requires_completed_local_analysis_and_live_grants(self):
        from hashlib import sha256
        raw=b"fixture"
        job,spec=plan(self.scope,"analysis","opencode","local-analysis",raw,4,50)
        grants=[self.g.fixture_grant("analysis"+str(n),Request(self.ctx,"agent",target,"local"),60)
                for n,target in enumerate((job.target(),spec.target()))]
        self.b.queue("analysis","opencode","local-analysis",raw,4,50,*grants)
        with self.assertRaises(Refused):
            self.b.receipt("analysis")
        self.b.dispatch()
        self.b.receive("analysis",self.event("analysis",1,"completed"))
        receipt=self.b.receipt("analysis")
        self.assertEqual(receipt.payload_digest,sha256(raw).hexdigest())
        self.assertEqual(receipt.spec_target,spec.target())
        self.g.revoke(grants[0].identity)
        with self.assertRaises(Refused):
            self.b.receipt("analysis")

    def test_schedule_grant_binds_payload_and_capability_independent_of_connector_grant(self):
        a=self.grants("one")
        changed=self.grants("one",payload=b"changed")
        with self.assertRaises(Refused):
            self.b.queue("one","opencode","plan",b"changed",4,50,a[0],changed[1])
        _,spec=plan(self.scope,"one","opencode","local-analysis",b"fixture",4,50)
        other=self.g.fixture_grant("other-capability",Request(self.ctx,"agent",spec.target(),"local"),60)
        with self.assertRaises(Refused):
            self.b.queue("one","opencode","local-analysis",b"fixture",4,50,a[0],other)
        self.b.queue("one","opencode","plan",b"fixture",4,50,*a)

    def test_root_revoked_during_queue_drains_reservation(self):
        grants=self.grants("one")
        original=self.g.claim
        def revoke(grant,req):
            original(grant,req)
            if grant is grants[0]:
                self.g.revoke(self.root.identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                self.b.queue("one","opencode","plan",b"fixture",4,50,*grants)
        self.assertEqual(self.b._scheduler.poll(),(0,0,0))
        self.assertEqual(self.b._records,{})
        for grant in grants:
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)

    def test_root_revoked_after_scheduler_dispatch_prevents_connector_creation(self):
        grants=self.queue("one")
        original=self.b._scheduler.dispatch
        def revoke():
            result=original()
            self.g.revoke(self.root.identity)
            return result
        with patch.object(self.b._scheduler,"dispatch",side_effect=revoke),patch("task_board.Connector") as connector:
            with self.assertRaises(Refused):
                self.b.dispatch()
            connector.assert_not_called()
        self.assertEqual(self.b._scheduler.poll(),(0,0,0))

    def test_later_connector_claim_revokes_earlier_batch_member(self):
        a=self.queue("a")
        b=self.queue("b")
        original=self.g.claim
        def revoke(grant,req):
            original(grant,req)
            if grant is b[1]:
                self.g.revoke(a[1].identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                self.b.dispatch()
        self.assertEqual(self.b.capacity(),(0,0,0))
        self.assertEqual([s.state for s in self.b.snapshot()],["held","held"])
