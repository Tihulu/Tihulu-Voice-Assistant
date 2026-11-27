# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_replan import AttemptCoordinator
from local_agent_retry_budget_session import RetryBudgetSession
from local_agent_vector_resources import Capacity,Demand,VectorResources
from local_agent_vector_planner import ModelMemory
from local_agent_vector_admission import VectorAdmission
import test_local_agent_retry_budget_session as retry
from test_local_agent_retry_deadline import Clock

class UnifiedWaveBudgetTests(unittest.TestCase):
    def fixture(self,prompt=600,backend="cpu",memory=2048):
        f=retry.RetryBudgetSessionTests()
        f.setUp()
        f.c=AttemptCoordinator(f.g,f.memory,"unified",f.f.workers,4096,2,owned_drain=True,strict_results=True)
        ledger=VectorResources(Capacity(4096,0))
        owner=VectorAdmission("vector-unified",ledger,f.f.workers,limit=2,backend=backend)
        inventory=(ModelMemory("qwen","cpu",Capacity(memory,0)),)
        clock=Clock()
        f.s=RetryBudgetSession(f.c,80,prompt_total=prompt,total_ns=100,worker_ns=20,clock=clock,
                               vector_owner=owner,vector_memory=inventory)
        return f,ledger,owner,clock

    def begin(self,f,policy="whole-plan"):
        p=f.s.prepare(f.ctx,f.models,"standard",prompt_policy=policy)
        return p,f.s.confirm(p,f.ctx,f.models)

    def finish(self,f,run,row):
        l,w,h=row
        f.finish(run,row)
        f.s.release_vector_after_exit(l)

    def test_full_wave_matches_and_drains(self):
        f,ledger,owner,c=self.fixture()
        p,run=self.begin(f)
        self.assertEqual(p.vector_digest,owner._active.plan.digest)
        for names in (("plan",),("a","b"),("review",)):
            rows=f.wave(p,run,names)
            self.assertEqual(ledger.snapshot()[1],Capacity(2048*len(names),0))
            for row in rows:
                self.finish(f,run,row)
            self.assertEqual(ledger.snapshot()[1],Capacity(0,0))
        self.assertTrue(owner.completed(owner._active))
        self.assertEqual(f.s.snapshot(),(80,64,0,16))

    def test_token_refusal_compensates_only_unstarted_vector(self):
        f,ledger,owner,c=self.fixture(prompt=237)
        p,run=self.begin(f,"wave")
        self.finish(f,run,f.wave(p,run,("plan",))[0])
        with self.assertRaisesRegex(Refused,"team_budget_prompt_exhausted"):
            f.s.reserve_wave(p,("a","b"),f.ctx,f.models)
        self.assertEqual(ledger.snapshot()[1],Capacity(0,0))
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        self.assertEqual(f.s.prompt_snapshot(),(237,14,0,223))
        self.assertTrue(run.drained())
        self.assertEqual(set(run._run._claims),{"plan"})

    def test_resource_refusal_does_not_charge_tokens(self):
        f,ledger,owner,c=self.fixture()
        p,run=self.begin(f)
        self.finish(f,run,f.wave(p,run,("plan",))[0])
        other,=ledger.reserve_wave("other",(Demand("x",Capacity(1,0)),))
        with self.assertRaisesRegex(Refused,"vector_resource_exhausted"):
            f.s.reserve_wave(p,("a","b"),f.ctx,f.models)
        self.assertEqual(ledger.snapshot()[1],Capacity(1,0))
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        self.assertTrue(run.drained())
        ledger.release_reaped(other)

    def test_deadline_holds_vector_until_managed_exit(self):
        f,ledger,owner,c=self.fixture()
        p,run=self.begin(f)
        l,w,h=f.wave(p,run,("plan",))[0]
        c.now=120
        with self.assertRaises(Refused):
            f.s.poll_deadline()
        for fake in (replace(l),l):
            with self.assertRaises(Refused):
                f.s.release_vector_after_exit(fake)
        f.s.settle_reaped(l,retry.envelope(l,w))
        with self.assertRaises(Refused):
            f.s.release_vector_after_exit(l)
        self.assertEqual(ledger.snapshot()[1],Capacity(2048,0))
        self.assertEqual(run.observe_exit(h,0,f.ctx,f.models),"DRAINED")
        with self.assertRaises(Refused):
            f.prepare()
        f.s.release_vector_after_exit(l)
        self.assertEqual(ledger.snapshot()[1],Capacity(0,0))
        self.assertEqual(f.prepare().remaining,64)

    def test_pending_family_retains_vector(self):
        f,ledger,owner,c=self.fixture()
        p,run=self.begin(f)
        l,w,h=f.wave(p,run,("plan",))[0]
        member=run.register_member(h,"child",f.ctx,f.models)
        f.s.stop()
        f.s.abandon_reaped(l)
        self.assertEqual(run.observe_exit(h,0,f.ctx,f.models),"PENDING")
        with self.assertRaises(Refused):
            f.s.release_vector_after_exit(l)
        self.assertEqual(run.observe_member_exit(member,f.ctx,f.models),"DRAINED")
        f.s.release_vector_after_exit(l)
        self.assertEqual(ledger.snapshot()[1],Capacity(0,0))

    def test_never_claimed_void_requires_token_void(self):
        f,ledger,owner,c=self.fixture()
        p,run=self.begin(f)
        (l,w),=f.s.reserve_wave(p,("plan",),f.ctx,f.models)
        f.s.stop()
        with self.assertRaises(Refused):
            f.s.release_vector_after_exit(l)
        f.s.void_not_dispatched(l)
        f.s.release_vector_after_exit(l)
        self.assertEqual(ledger.snapshot()[1],Capacity(0,0))
        self.assertEqual(f.s.snapshot(),(80,0,0,80))

    def test_plan_mismatch_and_backend_refusal(self):
        for args in (dict(memory=2049),dict(backend="accelerator")):
            f,ledger,owner,c=self.fixture(**args)
            with self.assertRaises(Refused):
                f.prepare()
            self.assertEqual(f.c._accepted,0)
            self.assertEqual(ledger.snapshot()[1],Capacity(0,0))

    def test_confirm_rechecks_vector_capacity(self):
        f,ledger,owner,c=self.fixture()
        p=f.prepare()
        ledger.resize(Capacity(4095,0))
        with self.assertRaises(Refused):
            f.s.confirm(p,f.ctx,f.models)
        self.assertEqual(f.c._accepted,0)
        self.assertEqual(f.s.snapshot(),(80,0,0,80))

    def test_failed_peer_holds_other_vector_until_drain(self):
        f,ledger,owner,c=self.fixture()
        p,run=self.begin(f)
        self.finish(f,run,f.wave(p,run,("plan",))[0])
        a,b=f.wave(p,run,("a","b"))
        f.s.abandon_reaped(a[0])
        run.observe_exit(a[2],1,f.ctx,f.models)
        f.s.release_vector_after_exit(a[0])
        self.assertEqual(ledger.snapshot()[1],Capacity(2048,0))
        f.s.abandon_reaped(b[0])
        run.observe_exit(b[2],0,f.ctx,f.models)
        f.s.release_vector_after_exit(b[0])
        self.assertEqual(ledger.snapshot()[1],Capacity(0,0))
        self.assertTrue(run.drained())
