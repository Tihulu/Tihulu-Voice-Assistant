import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from inference import Candidate, choose, envelope

class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task", "session", "profile", 1, "route", "SECURE_HYBRID", False)
        self.local = Candidate("jan", "llm", "local", "jan", True, True, 4096, 80)
        self.cloud = Candidate("cloud", "llm", "cloud", "fixture-provider", True, True, 0, 100)

    def test_local_route_never_silently_escalates(self):
        self.assertEqual(choose(self.ctx, (self.local, self.cloud), "llm", 4096, "local"), self.local)
        with self.assertRaises(Refused):
            choose(self.ctx, (self.local, self.cloud), "llm", 2048, "local", ("fixture-provider",))

    def test_cloud_explicitly_named_and_mode_checked(self):
        for ctx, approved in ((self.ctx, ()), (replace(self.ctx, offline=True), ("fixture-provider",)),
                              (replace(self.ctx, mode="LOCAL_ONLY"), ("fixture-provider",))):
            with self.assertRaises(Refused):
                choose(ctx, (self.cloud,), "llm", 0, "cloud", approved, purpose="consultation")
        self.assertEqual(choose(self.ctx, (self.cloud,), "llm", 0, "cloud", ("fixture-provider",), purpose="consultation"), self.cloud)

    def test_resource_and_compatibility_metadata_strict(self):
        for c in (replace(self.local, memory_mib=True), replace(self.local, rank=-1)):
            with self.assertRaises(Refused):
                choose(self.ctx, (c,), "llm", 4096, "local")
        for c in (replace(self.local, compatible=False), replace(self.local, healthy=False)):
            with self.assertRaises(Refused):
                choose(self.ctx, (c,), "llm", 4096, "local")
        with self.assertRaises(Refused):
            choose(self.ctx, (self.local, self.local), "llm", 4096, "local")

    def test_closed_disclosure_drops_nonselected_data(self):
        g = FixtureGateway(self.ctx, (profiles.Rule("cloud_disclosure", ("fixture-provider",), "allow"),), clock=profiles.PolicyClock())
        req = Request(self.ctx, "disclose", "fixture-provider", "local", "minimized")
        grant = g.fixture_grant("d", req, 60)
        out = envelope(g, grant, self.ctx, "fixture-provider", {"count": 5, "secret": "PRIVATE-CANARY", "raw": "raw fixture"}, ("count",))
        self.assertEqual(out.payload, b'{"count":5}')
        self.assertNotIn(b"CANARY", out.payload)
        for fields, values in ((("secret",), {"secret": "x"}), (("count",), {"count": True}),
                               (("count",), {"count": "5"}), (("min","max"), {"min": 9,"max": 1})):
            with self.assertRaises(Refused):
                envelope(g, grant, self.ctx, "fixture-provider", values, fields)
        g.revoke("d")
        with self.assertRaises(Refused):
            envelope(g, grant, self.ctx, "fixture-provider", {"count": 5}, ("count",))

    def test_secure_hybrid_primary_is_local_even_with_cloud_approval(self):
        with self.assertRaises(Refused):
            choose(self.ctx,(self.cloud,),"llm",0,"cloud",("fixture-provider",))
        self.assertEqual(choose(self.ctx,(self.local,self.cloud),"llm",4096,"local"),self.local)
        for task in ("stt","tts"):
            remote=replace(self.cloud,task=task)
            with self.assertRaises(Refused):
                choose(self.ctx,(remote,),task,0,"cloud",("fixture-provider",),purpose="consultation")

    def test_consultation_requires_secure_cloud_llm(self):
        for ctx in (replace(self.ctx,mode="STANDARD_HYBRID"),replace(self.ctx,mode="CLOUD_CHAT")):
            with self.assertRaises(Refused):
                choose(ctx,(self.cloud,),"llm",0,"cloud",("fixture-provider",),purpose="consultation")
            self.assertEqual(choose(ctx,(self.cloud,),"llm",0,"cloud",("fixture-provider",)),self.cloud)
        with self.assertRaises(Refused):
            choose(self.ctx,(self.local,),"llm",4096,"local",purpose="consultation")

    def test_cloud_chat_local_voice_route_is_not_executable(self):
        ctx=replace(self.ctx,mode="CLOUD_CHAT")
        for task in ("stt","tts","llm"):
            with self.assertRaises(Refused):
                choose(ctx,(replace(self.local,task=task),),task,4096,"local")
