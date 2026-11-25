import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Envelope
from adapter_protocol import disclosure_target
from provider_session import Account,CallPlan,ProviderSession

class ProviderSessionTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID",False)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("cloud_inference",(),"allow"),profiles.Rule("cloud_disclosure",(),"allow")),clock=profiles.PolicyClock())
        self.a=Account("provider-fixture","account-ref","api-key-reference","origin-fixture",1)
        self.data=b'{"count":5}'
        self.e=Envelope(self.a.provider,self.data,sha256(self.data).hexdigest())
        self.p=CallPlan(self.a,"model-fixture",self.e.digest)
        self.s=ProviderSession(self.g,self.ctx,(self.a,))
        self.pg=self.g.fixture_grant("p",Request(self.ctx,"provider",self.p.target(),"local","minimized"),60)
        self.dg=self.g.fixture_grant("d",Request(self.ctx,"disclose",disclosure_target(self.e),"local","minimized"),60)
    def test_independent_one_use_consents(self):
        self.assertEqual(self.s.prepare(self.p,self.e,self.pg,self.dg),("SIMULATED_PROVIDER_REQUEST_ONLY",self.data))
        with self.assertRaises(Refused):
            self.s.prepare(self.p,self.e,self.pg,self.dg)
    def test_account_origin_model_and_content_bound(self):
        for p in (replace(self.p,model="other"),replace(self.p,account=replace(self.a,origin="other")),
                  replace(self.p,account=replace(self.a,generation=2))):
            with self.assertRaises(Refused):
                self.s.prepare(p,self.e,self.pg,self.dg)
        changed=b'{"count":6}'
        e=replace(self.e,payload=changed,digest=sha256(changed).hexdigest())
        with self.assertRaises(Refused):
            self.s.prepare(replace(self.p,payload_digest=e.digest),e,self.pg,self.dg)
    def test_revocation_and_account_forget(self):
        self.g.revoke("d")
        with self.assertRaises(Refused):
            self.s.prepare(self.p,self.e,self.pg,self.dg)
        self.s.forget(self.a.reference)
        with self.assertRaises(Refused):
            self.s.prepare(self.p,self.e,self.pg,self.dg)
    def test_no_offline_or_local_only_provider(self):
        for ctx in (replace(self.ctx,offline=True),replace(self.ctx,mode="LOCAL_ONLY")):
            with self.assertRaises(Refused):
                ProviderSession(self.g,ctx,(self.a,))
        self.s.close()
        with self.assertRaises(Refused):
            self.s.prepare(self.p,self.e,self.pg,self.dg)
