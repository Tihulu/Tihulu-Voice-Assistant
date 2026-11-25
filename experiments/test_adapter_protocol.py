import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from inference import Candidate, Envelope
from adapter_protocol import LocalAdapter, prepare, disclosure_target, approved_payload

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID",False)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),
             profiles.Rule("cloud_disclosure",(),"allow")),clock=profiles.PolicyClock())
        self.c=Candidate("jan","llm","local","jan",True,True,4,80)
        self.call=prepare(self.c,"job","llm",b"mavi")
        self.grant=self.g.fixture_grant("e",Request(self.ctx,"engine",self.call.target,"local"),60)
    def adapter(self,data=b"mavi"):
        return LocalAdapter(self.g,self.ctx,self.c,"job","llm",data,self.grant)
    def test_exact_input_and_model_bound(self):
        a=self.adapter()
        a.event("job",1,"text","Merhaba")
        a.event("job",2,"completed",None)
        self.assertEqual(a.result(),b"Merhaba")
        with self.assertRaises(Refused):
            self.adapter(b"other")
        with self.assertRaises(Refused):
            a.event("job",3,"text","late")
    def test_invalid_sequence_and_oversized_output(self):
        for seq,value in ((True,"a"),(2,"a"),(1,"x"*16385)):
            a=self.adapter()
            with self.assertRaises(Refused):
                a.event("job",seq,"text",value)
            self.assertEqual(a.state,"held")
            with self.assertRaises(Refused):
                a.result()
    def test_cancel_and_revocation_drop_output(self):
        a=self.adapter()
        a.event("job",1,"text","mavi")
        a.cancel()
        with self.assertRaises(Refused):
            a.event("job",2,"completed",None)
        b=self.adapter()
        b.event("job",1,"text","mavi")
        b.event("job",2,"completed",None)
        self.g.revoke("e")
        with self.assertRaises(Refused):
            b.result()
    def test_exact_disclosure_content_and_destination(self):
        data=b'{"count":5}'
        env=Envelope("provider",data,sha256(data).hexdigest())
        grant=self.g.fixture_grant("d",Request(self.ctx,"disclose",disclosure_target(env),"local","minimized"),60)
        self.assertEqual(approved_payload(self.g,grant,self.ctx,env),data)
        changed=b'{"count":6}'
        for altered in (replace(env,destination="other"),replace(env,payload=changed,digest=sha256(changed).hexdigest())):
            with self.assertRaises(Refused):
                approved_payload(self.g,grant,self.ctx,altered)
        self.g.revoke("d")
        with self.assertRaises(Refused):
            approved_payload(self.g,grant,self.ctx,env)
    def test_canonical_closed_schema_rejects_forgery(self):
        for data in (b'{"count":5,"count":6}',b'{"secret":"canary"}',b'{"count":true}',b'{"count": 5}'):
            with self.assertRaises(Refused):
                disclosure_target(Envelope("provider",data,sha256(data).hexdigest()))
