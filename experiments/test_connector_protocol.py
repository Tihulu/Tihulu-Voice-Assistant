import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from connector_protocol import Connector, JobSpec
class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
        self.data=b"fixture plan"
        self.spec=JobSpec("opencode-fixture","s","j","plan",sha256(self.data).hexdigest())
        self.grant=self.g.fixture_grant("a",Request(self.ctx,"agent",self.spec.target(),"local"),60)
        self.c=Connector(self.g,self.ctx,"opencode-fixture","s")
    def test_owned_exact_job_and_terminal_event(self):
        h=self.c.begin(self.spec,self.grant,self.data)
        self.assertEqual(self.c.event(h,"s","j",1,"progress"),"running")
        self.assertEqual(self.c.event(h,"s","j",2,"completed"),"completed")
        with self.assertRaises(Refused):
            self.c.event(h,"s","j",3,"completed")
        with self.assertRaises(Refused):
            self.c.begin(self.spec,self.grant,self.data)
    def test_payload_and_capability_substitution(self):
        for spec,data in ((self.spec,b"other"),(replace(self.spec,capability="local-analysis"),self.data),
                          (replace(self.spec,job="other"),self.data)):
            with self.assertRaises(Refused):
                self.c.begin(spec,self.grant,data)
    def test_foreign_event_does_not_complete(self):
        h=self.c.begin(self.spec,self.grant,self.data)
        with self.assertRaises(Refused):
            self.c.event(object(),"s","j",1,"completed")
        self.assertEqual(self.c.status,"running")
        with self.assertRaises(Refused):
            self.c.event(h,"other","j",1,"completed")
        self.assertEqual(self.c.status,"held")
    def test_revoke_cancel_and_close(self):
        h=self.c.begin(self.spec,self.grant,self.data)
        self.g.revoke("a")
        self.c.cancel(h)
        with self.assertRaises(Refused):
            self.c.event(h,"s","j",1,"completed")
        self.c.close()
        self.assertEqual(self.c.status,"closed")
        with self.assertRaises(Refused):
            self.c.begin(self.spec,self.grant,self.data)

    def test_grant_cannot_start_duplicate_connector(self):
        self.c.begin(self.spec,self.grant,self.data)
        another=Connector(self.g,self.ctx,"opencode-fixture","s")
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            another.begin(self.spec,self.grant,self.data)
