# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,Refused
from inference import Candidate
from local_agent_planner import Worker,plan_agents

class LocalAgentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.small=Candidate("qwen-small","llm","local","jan",True,True,2048,60)
        self.large=Candidate("qwen-large","llm","local","jan",True,True,4096,90)
        self.cloud=Candidate("cloud","llm","cloud","remote",True,True,0,100)
        self.models=(self.small,self.large,self.cloud)
        self.workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),512,"execute"),
                      Worker("b",("plan",),512,"execute"),Worker("review",("a","b"),0,"review"))

    def test_dependency_waves_and_exact_budget(self):
        p=plan_agents(self.ctx,self.workers,self.models,8192,2,"economy")
        self.assertEqual(tuple(tuple(a.worker for a in w) for w in p.waves),
                         (("plan",),("a","b"),("review",)))
        self.assertTrue(all(sum(a.memory_mib for a in w)<=8192 for w in p.waves))
        self.assertTrue(all(a.provider=="jan" for w in p.waves for a in w))

    def test_agent_limit_and_budget_bound_concurrency(self):
        workers=tuple(Worker("w"+str(i),(),0,"execute") for i in range(10))
        p=plan_agents(self.ctx,workers,self.models,4096,6,"economy")
        self.assertEqual(tuple(map(len,p.waves)),(2,2,2,2,2))
        p=plan_agents(self.ctx,workers,self.models,65536,6)
        self.assertEqual(tuple(map(len,p.waves)),(6,4))

    def test_no_cloud_executor_or_implicit_fallback(self):
        for mode in ("LOCAL_ONLY","STANDARD_HYBRID","SECURE_HYBRID"):
            with self.assertRaises(Refused):
                plan_agents(replace(self.ctx,mode=mode),self.workers,(self.cloud,),8192)
        with self.assertRaises(Refused):
            plan_agents(replace(self.ctx,mode="CLOUD_CHAT"),self.workers,self.models,8192)

    def test_cycle_unknown_duplicate_and_self_dependencies(self):
        for workers in ((Worker("a",("b",),0,"plan"),Worker("b",("a",),0,"execute")),
                        (Worker("a",("missing",),0,"execute"),),(Worker("a",("a",),0,"execute"),),
                        (self.workers[0],self.workers[0])):
            with self.assertRaises(Refused):
                plan_agents(self.ctx,workers,self.models,8192)

    def test_health_memory_and_invalid_limits_fail_closed(self):
        for models in ((replace(self.small,healthy=False),),(replace(self.small,memory_mib=0),),
                       (replace(self.small,memory_mib=True),),(self.small,self.small)):
            with self.assertRaises(Refused):
                plan_agents(self.ctx,self.workers,models,8192)
        for limit in (False,0,11):
            with self.assertRaises(Refused):
                plan_agents(self.ctx,self.workers,self.models,8192,limit)
        with self.assertRaises(Refused):
            plan_agents(self.ctx,self.workers,self.models,1024)

    def test_deterministic_digest_binds_context_and_preference(self):
        a=plan_agents(self.ctx,self.workers,self.models,8192)
        b=plan_agents(self.ctx,self.workers[::-1],self.models[::-1],8192)
        self.assertEqual(a,b)
        for ctx,mode in ((replace(self.ctx,generation=2),"balanced"),(self.ctx,"economy")):
            self.assertNotEqual(a.digest,plan_agents(ctx,self.workers,self.models,8192,preference=mode).digest)
