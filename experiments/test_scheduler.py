import unittest
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from scheduler import Job, Scheduler

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(), "allow"),),clock=profiles.PolicyClock())
        self.grants=[self.g.fixture_grant("g"+str(i),Request(self.ctx,"agent","agent"+str(i),"local"),60) for i in range(4)]
        self.s=Scheduler(self.g,self.ctx,2,8)
    def add(self,i,mem,priority):
        self.s.queue(Job("j"+str(i),"agent"+str(i),mem,priority),self.grants[i])
    def test_priority_slots_and_memory(self):
        self.add(0,6,1)
        self.add(1,4,9)
        self.add(2,4,8)
        handles=self.s.dispatch()
        self.assertEqual([j for _,j in handles],["j1","j2"])
        self.assertEqual(self.s.dispatch(),())
        for h,_ in handles:
            self.s.finish(h)
        self.assertEqual(self.s.dispatch()[0][1],"j0")
    def test_revocation_queued_and_running(self):
        self.add(0,4,9)
        self.add(1,4,8)
        self.g.revoke("g1")
        [(h,j)]=self.s.dispatch()
        self.assertEqual(j,"j0")
        self.g.revoke("g0")
        with self.assertRaises(Refused):
            self.s.finish(h)
        self.assertEqual(self.s.dispatch(),())
    def test_cancel_close_and_wrong_grant(self):
        with self.assertRaises(Refused):
            self.s.queue(Job("wrong","agent1",1,1),self.grants[0])
        self.add(0,4,1)
        [(h,_)]=self.s.dispatch()
        self.s.cancel(h)
        with self.assertRaises(Refused):
            self.s.finish(h)
        self.s.close()
        with self.assertRaises(Refused):
            self.add(1,4,1)
