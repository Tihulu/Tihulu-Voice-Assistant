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

    def test_revoked_running_reservation_reclaims_capacity_on_dispatch(self):
        self.add(0,8,9)
        self.add(1,8,8)
        [(old,_)]=self.s.dispatch()
        self.g.revoke("g0")
        self.assertEqual(self.s.poll(),(1,0,0))
        [(new,identity)]=self.s.dispatch()
        self.assertEqual(identity,"j1")
        with self.assertRaises(Refused):
            self.s.finish(old)
        self.assertEqual(self.s.finish(new),"j1")

    def test_queued_cancel_and_close_revoke_owned_grants(self):
        j0=Job("j0","agent",8,9)
        j1=Job("j1","agent",8,8)
        g0=self.grant(j0,"g0")
        g1=self.grant(j1,"g1")
        self.s.queue(j0,g0)
        self.s.queue(j1,g1)
        self.s.cancel_queued("j1")
        with self.assertRaises(Refused):
            self.g.checkpoint(g1,Request(self.ctx,"agent",j1.target(),"local"))
        self.s.dispatch()
        self.s.close()
        with self.assertRaises(Refused):
            self.g.checkpoint(g0,Request(self.ctx,"agent",j0.target(),"local"))
        self.assertEqual(self.s.poll(),(0,0,0))

    def test_running_cancel_revokes_permission(self):
        j=Job("j","agent",8,9)
        g=self.grant(j,"g")
        self.s.queue(j,g)
        [(h,_)]=self.s.dispatch()
        self.s.cancel(h)
        with self.assertRaises(Refused):
            self.g.checkpoint(g,Request(self.ctx,"agent",j.target(),"local"))
        self.assertEqual(self.s.poll(),(0,0,0))

    def test_job_spec_digest_bound_to_resource_grant(self):
        job=Job("j","agent",4,5,"a"*64)
        grant=self.grant(job,"bound-spec")
        with self.assertRaises(Refused):
            self.s.queue(replace(job,spec_target="b"*64),grant)
        with self.assertRaises(Refused):
            self.s.queue(replace(job,spec_target=[]),grant)
        self.s.queue(job,grant)

    def test_reservation_snapshot_reflects_revocation_and_memory(self):
        self.add(0,4,9)
        self.add(1,4,8)
        self.add(2,4,7)
        self.s.dispatch()
        self.assertEqual(self.s.reservations(),(("j2",),("j0","j1"),8))
        self.g.revoke("g1")
        self.assertEqual(self.s.reservations(),(("j2",),("j0",),4))
