# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,Refused
from inference import Candidate
from local_agent_planner import Worker
from local_agent_vector_resources import Capacity
from local_agent_vector_planner import ModelMemory,vector_plan

class VectorPlannerTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.models=(Candidate("big","llm","local","jan",True,True,12,100),
                     Candidate("small","llm","local","jan",True,True,6,60),
                     Candidate("cpu","llm","local","jan",True,True,8,90))
        self.mem=(ModelMemory("big","accelerator",Capacity(4,8)),
                  ModelMemory("small","accelerator",Capacity(2,4)),
                  ModelMemory("cpu","cpu",Capacity(8,0)))
        self.workers=tuple(Worker("w"+str(i),(),0,"execute") for i in range(10))

    def plan(self,capacity=Capacity(64,16),**kw):
        return vector_plan(self.ctx,kw.pop("workers",self.workers),kw.pop("models",self.models),
                           kw.pop("memory",self.mem),capacity,**kw)

    def test_dynamic_limit_one_to_ten(self):
        for n in range(1,11):
            p=self.plan(Capacity(100,100),limit=n)
            self.assertEqual(len(p.waves[0]),n)
            self.assertEqual(sum(map(len,p.waves)),10)

    def test_economy_and_performance_deterministic(self):
        e=self.plan(preference="economy",limit=10)
        p=self.plan(preference="performance",limit=10)
        self.assertEqual(len(e.waves[0]),4)
        self.assertTrue(all(a.model=="small" for w in e.waves for a in w))
        self.assertEqual(len(p.waves[0]),2)
        self.assertTrue(all(a.model=="big" for w in p.waves for a in w))
        self.assertNotEqual(e.digest,p.digest)

    def test_ram_and_vram_capacity_both_enforced(self):
        for capacity in (Capacity(4,100),Capacity(100,4),Capacity(5,9)):
            p=self.plan(capacity,preference="economy",limit=10)
            for wave in p.waves:
                self.assertLessEqual(sum(a.capacity.ram_mib for a in wave),capacity.ram_mib)
                self.assertLessEqual(sum(a.capacity.vram_mib for a in wave),capacity.vram_mib)

    def test_no_implicit_cpu_fallback(self):
        with self.assertRaisesRegex(Refused,"vector_plan_capacity"):
            self.plan(Capacity(64,0))
        p=self.plan(Capacity(64,0),backend="cpu")
        self.assertTrue(all(a.backend=="cpu" for w in p.waves for a in w))
        p=self.plan(Capacity(64,0),backend="mixed")
        self.assertTrue(all(a.backend=="cpu" for w in p.waves for a in w))

    def test_dependency_barrier(self):
        workers=(Worker("plan",(),1,"plan"),Worker("a",("plan",),0,"execute"),
                 Worker("b",("plan",),0,"execute"),Worker("review",("a","b"),0,"review"))
        p=self.plan(workers=workers)
        self.assertEqual(tuple(tuple(a.worker for a in w) for w in p.waves),(("plan",),("a","b"),("review",)))
        self.assertEqual(p.waves[0][0].capacity.ram_mib,3)

    def test_cycle_and_unknown_parent(self):
        for workers in ((Worker("a",("b",),0,"execute"),Worker("b",("a",),0,"execute")),
                        (Worker("a",("missing",),0,"execute"),)):
            with self.assertRaises(Refused):
                self.plan(workers=workers)

    def test_cloud_and_unhealthy_models_never_selected(self):
        models=tuple(replace(c,locality="cloud") if c.identity=="big" else
                     replace(c,healthy=False) if c.identity=="small" else c for c in self.models)
        with self.assertRaises(Refused):
            self.plan(models=models)
        self.assertTrue(all(a.model=="cpu" for w in self.plan(models=models,backend="mixed").waves for a in w))

    def test_profile_must_cover_declared_model_memory(self):
        for memory in ((replace(self.mem[0],capacity=Capacity(1,1)),)+self.mem[1:],
                       (replace(self.mem[0],backend="cpu"),)+self.mem[1:],
                       (self.mem[0],self.mem[0],self.mem[2]), self.mem[:2]):
            with self.assertRaises(Refused):
                self.plan(memory=memory)

    def test_digest_binds_profile_capacity_backend_and_context(self):
        p=self.plan()
        self.assertEqual(p,self.plan(models=self.models[::-1],memory=self.mem[::-1],workers=self.workers[::-1]))
        for q in (self.plan(Capacity(63,16)),self.plan(backend="mixed"),
                  self.plan(memory=(replace(self.mem[0],capacity=Capacity(5,8)),)+self.mem[1:]),
                  self.plan(models=(replace(self.models[0],rank=99),)+self.models[1:])):
            self.assertNotEqual(p.digest,q.digest)

    def test_worker_overhead_and_invalid_limit(self):
        with self.assertRaises(Refused):
            self.plan(Capacity(2,4),workers=(Worker("a",(),1,"execute"),))
        for n in (0,11,True):
            with self.assertRaises(Refused):
                self.plan(limit=n)
