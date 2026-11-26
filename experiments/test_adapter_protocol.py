import unittest
import json
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
    def adapter(self,data=b"mavi",grant=None):
        return LocalAdapter(self.g,self.ctx,self.c,"job","llm",data,self.grant if grant is None else grant)
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
        for i,(seq,value) in enumerate(((True,"a"),(2,"a"),(1,"x"*16385))):
            grant=self.g.fixture_grant("bad"+str(i),Request(self.ctx,"engine",self.call.target,"local"),60)
            a=self.adapter(grant=grant)
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
        with self.assertRaises(Refused):
            self.adapter()
        fresh=self.g.fixture_grant("fresh",Request(self.ctx,"engine",self.call.target,"local"),60)
        b=self.adapter(grant=fresh)
        b.event("job",1,"text","mavi")
        b.event("job",2,"completed",None)
        self.g.revoke("fresh")
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

    def test_same_grant_cannot_start_two_adapters(self):
        a=self.adapter()
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            self.adapter()
        a.event("job",1,"text","mavi")
        a.event("job",2,"completed",None)
        self.assertEqual(a.result(),b"mavi")

    def test_simultaneous_claim_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def start(_):
            barrier.wait(timeout=5)
            try:
                self.adapter()
                return True
            except Refused:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(start,range(2))),[False,True])

    def test_concurrent_cancel_and_completion_leave_no_result(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        a=self.adapter()
        a.event("job",1,"text","mavi")
        barrier=Barrier(2)
        def complete():
            barrier.wait(timeout=5)
            try:
                a.event("job",2,"completed",None)
            except Refused:
                pass
        def cancel():
            barrier.wait(timeout=5)
            a.cancel()
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(complete)
            two=pool.submit(cancel)
            one.result(timeout=5)
            two.result(timeout=5)
        self.assertEqual(a.state,"cancelled")
        with self.assertRaises(Refused):
            a.result()
        with self.assertRaises(Refused):
            self.g.checkpoint(self.grant,Request(self.ctx,"engine",self.call.target,"local"))

    def test_payload_consent_cannot_be_replayed(self):
        data=b'{"count":5}'
        env=Envelope("provider",data,sha256(data).hexdigest())
        req=Request(self.ctx,"disclose",disclosure_target(env),"local","minimized")
        grant=self.g.fixture_grant("one",req,60)
        self.assertEqual(approved_payload(self.g,grant,self.ctx,env),data)
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            approved_payload(self.g,grant,self.ctx,env)

    def test_payload_revocation_at_claim_refuses_return(self):
        from unittest.mock import patch
        data=b'{"count":5}'
        env=Envelope("provider",data,sha256(data).hexdigest())
        req=Request(self.ctx,"disclose",disclosure_target(env),"local","minimized")
        grant=self.g.fixture_grant("one",req,60)
        original=self.g.claim
        def revoke(g,r):
            original(g,r)
            self.g.revoke(g.identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                approved_payload(self.g,grant,self.ctx,env)

    def frame(self,grant,sequence,kind,value,**changes):
        obj={"grant":grant.identity,"target":grant.request.target,"identity":"job",
             "sequence":sequence,"kind":kind,"value":value}
        obj.update(changes)
        return json.dumps(obj,ensure_ascii=False).encode("utf-8")

    def test_strict_stream_multilingual_frames(self):
        a=self.adapter()
        for i,text in enumerate(("Merhaba ","Guten Tag ","Hello"),1):
            a.frame(self.frame(self.grant,i,"text",text))
        a.frame(self.frame(self.grant,4,"completed",None))
        self.assertEqual(a.result(),b"Merhaba Guten Tag Hello")
        with self.assertRaises(Refused):
            a.frame(self.frame(self.grant,5,"text","late"))

    def test_stale_stream_invocation_cannot_enter_new_call(self):
        a=self.adapter()
        old=self.frame(self.grant,1,"text","old")
        a.cancel()
        fresh=self.g.fixture_grant("fresh",self.grant.request,60)
        b=self.adapter(grant=fresh)
        with self.assertRaisesRegex(Refused,"stream_invocation_mismatch"):
            b.frame(old)
        self.assertEqual(b.state,"held")
        with self.assertRaises(Refused):
            b.result()

    def test_stream_malformed_duplicate_or_unapproved_fields_fail_closed(self):
        for i,raw in enumerate((b'{"grant":"x","grant":"y"}',b'[]',b'\xff',b'x'*32769,
                                self.frame(self.grant,1,"text","x",tool_calls=[]))):
            grant=self.g.fixture_grant("frame"+str(i),self.grant.request,60)
            a=self.adapter(grant=grant)
            with self.assertRaises(Refused):
                a.frame(raw)
            self.assertEqual(a.state,"held")

    def test_stream_wrong_target_or_sequence_discards_partial_result(self):
        for i,changes in enumerate(({"target":"0"*64},{"sequence":3},{"kind":"tool_call"})):
            grant=self.g.fixture_grant("mismatch"+str(i),self.grant.request,60)
            a=self.adapter(grant=grant)
            a.frame(self.frame(grant,1,"text","partial"))
            obj={"grant":grant.identity,"target":grant.request.target,"identity":"job",
                 "sequence":2,"kind":"text","value":"next"}
            obj.update(changes)
            with self.assertRaises(Refused):
                a.frame(json.dumps(obj).encode())
            with self.assertRaises(Refused):
                a.result()

    def test_call_is_readonly_and_revocation_refuses_frame(self):
        a=self.adapter()
        with self.assertRaises(AttributeError):
            a.call=replace(a.call,identity="other")
        self.g.revoke(self.grant.identity)
        with self.assertRaises(Refused):
            a.frame(self.frame(self.grant,1,"text","x"))
