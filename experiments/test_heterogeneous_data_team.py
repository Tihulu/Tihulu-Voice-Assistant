# SPDX-License-Identifier: AGPL-3.0-only
import unittest
import json
from dataclasses import replace
from hashlib import sha256
from gateway import Context,FixtureGateway,Refused
from tihulu_jarvis import profiles
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer
from heterogeneous_team import TeamTask
from heterogeneous_team_admission import TeamAdmission
from heterogeneous_data_tools import DataCall
from heterogeneous_data_team import DataTeamRun
from local_agent_resources import ResourceLedger
from test_vision_image_input import png

class DataTeamRunTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,tuple(profiles.Rule(a,(),"allow") for a in
                            ("filesystem_read","host_shell","publish_artifact")),clock=profiles.PolicyClock())
        self.models=(ModelOffer("text","jan","local",("text","structured_tools"),1024,80,True,True),)
        self.available=("file_read","typed_engine","reasoning")
        def task(name,role,channel,depends=()):
            p=AgentProfile(name,1,role,"local",(channel,),"economy","","",0,128)
            return TeamTask(name,p,(channel,),depends)
        self.tasks=(task("text","file","file_read"),task("csv","data_science","typed_engine"),
                    task("image","application","typed_engine"),task("review","reviewer","reasoning",("text","csv","image")))
        self.calls=(DataCall("text","read_text",b"public fixture"),DataCall("csv","csv_stats",b"value\n7\n-2\n10\n"),
                    DataCall("image","png_stats",png()),DataCall("review","report",b""))
        self.ledger=ResourceLedger(4096)
    def make(self,calls=None,identity="team"):
        a=TeamAdmission(self.g,identity)
        args=(self.ctx,self.tasks,self.available,self.models,4096,512)
        p=a.confirm(a.prepare(*args),*args)
        return DataTeamRun(a,p,self.calls if calls is None else calls,self.ledger,memory_budget=4096,token_budget=512)
    def start(self):
        self.run=self.make()
        self.grants={t.identity:self.g.fixture_grant("grant-"+t.identity,self.run.request(t.identity),60)
                     for t in self.tasks}
    def execute(self,task,grant=None,models=None):
        return self.run.execute(task,grant or self.grants[task],self.ctx,self.available,models or self.models)
    def output(self,task):
        return self.run.output(task,self.ctx,self.available,self.models)
    def test_actual_four_role_results_and_transitive_receipts(self):
        self.start()
        for t in ("csv","image","text","review"):
            r=self.execute(t)
            self.assertEqual(len(r.input_digest),64)
            self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        r,p=self.output("review")
        self.assertEqual(r.payload_digest,sha256(p).hexdigest())
        rows=json.loads(p)["results"]
        self.assertEqual([x["task"] for x in rows],["csv","image","text"])
        self.assertEqual(rows[0]["result"]["sum"],15)
        self.assertEqual(rows[1]["result"]["width"],2)
        self.assertEqual(rows[2]["result"]["words"],2)
    def test_no_grants_on_plan_and_unsupported_channel(self):
        self.run=self.make()
        self.assertEqual(self.g._grants,{})
        for calls in (self.calls[:-1],self.calls+(self.calls[0],),
                      (replace(self.calls[0],operation="csv_stats"),)+self.calls[1:]):
            with self.assertRaises(Refused):
                self.make(calls,identity="bad"+str(len(self.g._agent_attempts)))
    def test_wave_and_publication_barrier(self):
        self.start()
        with self.assertRaises(Refused):
            self.execute("review")
        self.execute("csv")
        with self.assertRaises(Refused):
            self.output("csv")
        with self.assertRaises(Refused):
            self.execute("csv")
        self.execute("text")
        self.execute("image")
        self.execute("review")
        self.output("csv")
    def test_cross_task_grant_stops_without_result(self):
        self.start()
        with self.assertRaises(Refused):
            self.execute("csv",self.grants["image"])
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        with self.assertRaises(Refused):
            self.execute("image")
    def test_revoked_parent_denies_report_and_clears_results(self):
        self.start()
        for t in ("csv","image","text"):
            self.execute(t)
        self.g.revoke("grant-csv")
        with self.assertRaises(Refused):
            self.execute("review")
        self.assertEqual(self.run._results,{})
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
    def test_inventory_drift_stops_before_next_execution(self):
        self.start()
        self.execute("csv")
        with self.assertRaises(Refused):
            self.execute("image",models=(replace(self.models[0],rank=81),))
        self.assertEqual(self.run._results,{})
    def test_failed_real_computation_releases_reservation(self):
        self.calls=(self.calls[0],replace(self.calls[1],data=b"value\nbad\n"))+self.calls[2:]
        self.start()
        with self.assertRaises(Refused):
            self.execute("csv")
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        with self.assertRaises(Refused):
            self.execute("text")
    def test_stop_revokes_published_chain(self):
        self.start()
        for t in ("csv","image","text","review"):
            self.execute(t)
        self.output("review")
        self.run.stop()
        with self.assertRaises(Refused):
            self.output("review")
    def test_source_change_requires_distinct_grant(self):
        a=self.make(identity="first")
        b=self.make(self.calls[:1]+(replace(self.calls[1],data=b"value\n1\n"),)+self.calls[2:],identity="second")
        self.assertNotEqual(a.request("csv"),b.request("csv"))
        g=self.g.fixture_grant("old",a.request("csv"),60)
        with self.assertRaises(Refused):
            b.execute("csv",g,self.ctx,self.available,self.models)
