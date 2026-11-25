import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from scheduler import Job, Scheduler

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(), "allow"),),clock=profiles.PolicyClock())
        self.s=Scheduler(self.g,self.ctx,2,8)
    def grant(self,job,identity):
        return self.g.fixture_grant(identity,Request(self.ctx,"agent",job.target(),"local"),60)
    def add(self,i,mem,priority):
        job=Job("j"+str(i),"agent"+str(i),mem,priority)
        self.s.queue(job,self.grant(job,"g"+str(i)))
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
        job=Job("j0","agent0",4,1)
        grant=self.grant(job,"wrong")
        with self.assertRaises(Refused):
            self.s.queue(Job("wrong","agent1",1,1),grant)
        self.add(0,4,1)
        [(h,_)]=self.s.dispatch()
        self.s.cancel(h)
        with self.assertRaises(Refused):
            self.s.finish(h)
        self.s.close()
        with self.assertRaises(Refused):
            self.add(1,4,1)
    def test_job_identity_resource_budget_and_priority_bound(self):
        job=Job("j0","agent0",4,1)
        grant=self.grant(job,"bound")
        for altered in (replace(job,identity="j1"),replace(job,memory=5),replace(job,priority=99)):
            with self.assertRaises(Refused):
                self.s.queue(altered,grant)
        self.s.queue(job,grant)
        other=Scheduler(self.g,self.ctx,2,8)
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            other.queue(job,grant)
    def test_concurrent_dispatch_never_exceeds_capacity(self):
        for i in range(4):
            self.add(i,4,10-i)
        barrier=Barrier(2)
        def run(_):
            barrier.wait(timeout=5)
            return self.s.dispatch()
        with ThreadPoolExecutor(max_workers=2) as pool:
            batches=list(pool.map(run,range(2)))
        handles=[h for batch in batches for h,_ in batch]
        self.assertEqual(len(handles),2)
        for h in handles:
            self.s.finish(h)
        self.assertEqual(len(self.s.dispatch()),2)
    def test_invalid_handle_does_not_release_capacity(self):
        self.add(0,8,1)
        [(h,_)]=self.s.dispatch()
        for invalid in ([],object()):
            with self.assertRaises(Refused):
                self.s.cancel(invalid)
        self.assertEqual(self.s.finish(h),"j0")
