import unittest
import json
from hashlib import sha256
from dataclasses import replace
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from inference import Candidate
from inventory import Inventory, Entry
from research import canonical
from integrated_workflow import FixtureWorkflow, REPORT_STEP
from task_chain import Artifact, step_target

class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID",False)
        self.g=FixtureGateway(self.ctx,tuple(profiles.Rule(a,(),"allow") for a in
            ("external_network","filesystem_read","host_shell","cloud_disclosure","publish_artifact")),
            clock=profiles.PolicyClock())
        self.url="https://example.com/research/fixture"
        self.destination="fixture-provider"
        self.body=b"time_s,temperature_c\n0,20\n10,22\n20,24\n30,26\n40,28\n"
        self.i=Inventory()
        self.i.register(Entry("jan","a"*64,"llm","jan",True,False))
        self.candidates=(Candidate("jan","llm","local","jan",True,True,4,80),)
        self.grants={}
        for name,action,target,classification in (
            ("research","research",canonical(self.url).identity,"public"),
            ("engine","engine",step_target(REPORT_STEP,Artifact("csv",sha256(self.body).hexdigest(),"")),"none"),
            ("agent","agent","analysis-fixture","none"),
            ("disclose","disclose",self.destination,"minimized"),
            ("publish","publish","temperature-report","none")):
            req=Request(self.ctx,action,target,"local",classification)
            self.grants[name]=self.g.fixture_grant(name,req,60)
        self.w=FixtureWorkflow(self.g,self.ctx,self.i,self.candidates)

    def run_fixture(self, **kw):
        args=dict(url=self.url,root="https://example.com/research",body=self.body,
                  addresses=("8.8.8.8",),peer="8.8.8.8",
                  headers=(("Content-Type","text/plain; charset=utf-8"),("Content-Length",str(len(self.body)))),
                  budget=8,destination=self.destination,grants=self.grants)
        args.update(kw)
        return self.w.run(**args)

    def test_four_motor_report_matches_real_engine(self):
        result=self.run_fixture()
        self.assertEqual(json.loads(result.table),{"count":5,"mean_c":24,"min_c":20,"max_c":28,"delta_c":8,"duration_s":40})
        self.assertEqual(result.disclosure,b'{"count":5}')
        self.assertEqual(result.source_digest,sha256(self.body).hexdigest())
        self.assertEqual(result.table_digest,sha256(result.table).hexdigest())
        self.assertEqual(result.chart_digest,sha256(result.svg).hexdigest())
        self.assertIn(b"<polyline",result.svg)
        self.assertEqual(result.report_digest,sha256(result.table+b"\n"+result.svg).hexdigest())
        self.assertEqual(self.w.result(),result)
        self.assertEqual(self.i.removal_plan("jan"),"REFERENCE_ONLY")

    def test_revoked_research_invalidates_retained_report(self):
        self.run_fixture()
        self.g.revoke("research")
        with self.assertRaises(Refused):
            self.w.result()
        self.assertEqual(self.w.state,"held")

    def test_no_disclosure_on_revocation_during_compute(self):
        from tihulu_jarvis.temperature_engine import analyze_temperature as actual
        def revoked(*args,**kwargs):
            series=actual(*args,**kwargs)
            self.g.revoke("disclose")
            return series
        with patch("integrated_workflow.analyze_temperature",side_effect=revoked), patch("integrated_workflow.envelope") as send:
            with self.assertRaises(Refused):
                self.run_fixture()
            send.assert_not_called()
        self.assertEqual(self.w.state,"held")
        self.assertEqual(self.i.removal_plan("jan"),"REFERENCE_ONLY")
        with self.assertRaises(Refused):
            self.w.result()

    def test_cross_scope_and_peer_refused(self):
        for case in ("wrong_grant","wrong_peer"):
            with self.subTest(case=case):
                self.w=FixtureWorkflow(self.g,self.ctx,self.i,self.candidates)
                if case=="wrong_grant":
                    args={"grants":dict(self.grants,engine=self.grants["agent"])}
                else:
                    args={"peer":"1.1.1.1"}
                with self.assertRaises(Refused):
                    self.run_fixture(**args)
                self.assertEqual(self.i.removal_plan("jan"),"REFERENCE_ONLY")

    def test_close_and_replay(self):
        self.run_fixture()
        with self.assertRaises(Refused):
            self.run_fixture()
        self.w.close()
        with self.assertRaises(Refused):
            self.w.result()

    def test_no_route_fallback_and_no_retained_result(self):
        with self.assertRaises(Refused):
            self.run_fixture(budget=1)
        self.assertEqual(self.w.state,"held")
        with self.assertRaises(Refused):
            self.w.result()

    def test_same_source_url_changed_bytes_requires_new_grant(self):
        changed=self.body.replace(b"0,20",b"0,21")
        with self.assertRaises(Refused):
            self.run_fixture(body=changed)
        self.assertEqual(self.w.state,"held")

    def test_candidate_inventory_mismatch_and_zero_budget(self):
        self.w=FixtureWorkflow(self.g,self.ctx,self.i,(replace(self.candidates[0],provider="native"),))
        with self.assertRaises(Refused):
            self.run_fixture()
        self.assertEqual(self.i.removal_plan("jan"),"REFERENCE_ONLY")
        self.w=FixtureWorkflow(self.g,self.ctx,self.i,self.candidates)
        with self.assertRaises(Refused):
            self.run_fixture(budget=0)
        self.assertEqual(self.w.state,"held")

    def test_dispatch_refusal_drains_lease_and_retains_no_result(self):
        with patch("integrated_workflow.Scheduler.dispatch",return_value=()):
            with self.assertRaisesRegex(Refused,"dispatch_refused"):
                self.run_fixture()
        self.assertEqual(self.w.state,"held")
        self.assertEqual(self.i.removal_plan("jan"),"REFERENCE_ONLY")
        with self.assertRaises(Refused):
            self.w.result()
