import json
import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from task_board import TaskBoard,plan,scoped

class TaskBoardTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
        self.b=TaskBoard(self.g,self.ctx,"room",2,10)
        self.addCleanup(self.b.close)
        self.n=0
    def grants(self,i,memory=4,priority=50,payload=b"fixture"):
        job,spec=plan("room",i,"opencode","plan",payload,memory,priority)
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
        return json.dumps(dict(session="room",job=scoped("room",i),sequence=seq,kind=kind)).encode()
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
        other=TaskBoard(self.g,self.ctx,"other",2,10)
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
