import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Envelope
from adapter_protocol import disclosure_target
from provider_session import Account,CallPlan,ProviderSession,payload_target
from unittest.mock import patch

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
        self.dg=self.g.fixture_grant("d",Request(self.ctx,"disclose",payload_target(self.p,self.e),"local","minimized"),60)
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

    def test_same_provider_different_account_requires_distinct_payload_consent(self):
        other=replace(self.a,reference="second-account")
        plan=replace(self.p,account=other)
        s=ProviderSession(self.g,self.ctx,(self.a,other))
        pg=self.g.fixture_grant("other",Request(self.ctx,"provider",plan.target(),"local","minimized"),60)
        with self.assertRaises(Refused):
            s.prepare(plan,self.e,pg,self.dg)
        self.assertNotEqual(payload_target(plan,self.e),payload_target(self.p,self.e))
        self.s.prepare(self.p,self.e,self.pg,self.dg)

    def test_payload_claim_failure_revokes_only_owned_first_grant(self):
        original=self.g.claim
        def fail(grant,req):
            if grant is self.dg:
                raise Refused("fixture_claim_failure")
            return original(grant,req)
        with patch.object(self.g,"claim",side_effect=fail):
            with self.assertRaises(Refused):
                self.s.prepare(self.p,self.e,self.pg,self.dg)
        with self.assertRaises(Refused):
            self.g.checkpoint(self.pg,self.pg.request)
        self.g.checkpoint(self.dg,self.dg.request)

    def test_account_forget_revokes_successful_call_permissions(self):
        self.s.prepare(self.p,self.e,self.pg,self.dg)
        self.s.forget(self.a.reference)
        for grant in (self.pg,self.dg):
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)

    def test_close_during_first_claim_prevents_second_claim(self):
        original=self.g.claim
        def close(grant,req):
            original(grant,req)
            if grant is self.pg:
                self.s.close()
        with patch.object(self.g,"claim",side_effect=close):
            with self.assertRaises(Refused):
                self.s.prepare(self.p,self.e,self.pg,self.dg)
        with self.assertRaises(Refused):
            self.g.checkpoint(self.pg,self.pg.request)
        self.g.claim(self.dg,self.dg.request)

    def test_mismatched_gateway_context_refused_at_construction(self):
        with self.assertRaises(Refused):
            ProviderSession(self.g,replace(self.ctx,generation=2),(self.a,))

    def test_generic_provider_only_consent_not_account_bound(self):
        generic=self.g.fixture_grant("generic",Request(self.ctx,"disclose",disclosure_target(self.e),"local","minimized"),60)
        with self.assertRaises(Refused):
            self.s.prepare(self.p,self.e,self.pg,generic)
        self.s.prepare(self.p,self.e,self.pg,self.dg)
