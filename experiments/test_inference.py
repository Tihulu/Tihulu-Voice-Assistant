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
                choose(ctx, (self.cloud,), "llm", 0, "cloud", approved)
        self.assertEqual(choose(self.ctx, (self.cloud,), "llm", 0, "cloud", ("fixture-provider",)), self.cloud)

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
