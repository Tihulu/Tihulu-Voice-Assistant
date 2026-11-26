# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from search_result import SearchQuery, SearchFixture, SearchResult, Hit


class SearchResultTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task", "session", "profile", 1, "route",
                           "LOCAL_ONLY", False, True)
        self.query = SearchQuery("fixture1", "İstanbul weather")
        self.req = self.query.call().request(self.ctx)
        self.hit = Hit("https://example.org/weather", "Weather", "Synthetic source")
        self.result = SearchResult(self.req.target, (self.hit,))

    def gateway(self):
        return FixtureGateway(self.ctx, (profiles.Rule("external_network",
                              (self.req.target,), "allow"),), clock=profiles.PolicyClock())

    def fixture(self):
        g = self.gateway()
        grant = g.fixture_grant("search", self.req, 60)
        return g, grant, SearchFixture(g, grant, self.ctx, self.query)

    def test_exact_query_and_provider_version(self):
        g = self.gateway()
        grant = g.fixture_grant("search", self.req, 60)
        for changed in (replace(self.query, text="İstanbul weather "),
                        replace(self.query, provider_version="fixture2")):
            with self.assertRaisesRegex(Refused, "request_mismatch"):
                SearchFixture(g, grant, self.ctx, changed)
        job = SearchFixture(g, grant, self.ctx, self.query)
        job.complete(self.result)
        self.assertEqual(job.result(), ("SIMULATED_ONLY", self.result))
        with self.assertRaises(Refused):
            SearchFixture(g, grant, self.ctx, self.query)

    def test_untrusted_hits_do_not_grant_navigation(self):
        g, grant, job = self.fixture()
        job.complete(self.result)
        from capabilities import CapabilityCall
        nav = CapabilityCall("firefox", "fixture", "navigate",
                             (("profile","owned"), ("tab","tab1"), ("url",self.hit.url)))
        with self.assertRaises(Refused):
            g.claim(grant, nav.request(self.ctx))
        self.assertEqual(job.result()[1], self.result)

    def test_bad_result_identity_duplicate_and_bounds(self):
        for bad in (replace(self.result, request_target="a"*64),
                    replace(self.result, hits=(self.hit, self.hit)),
                    replace(self.result, hits=(self.hit,)*13),
                    replace(self.result, hits=(replace(self.hit, snippet="x"*1001),)),
                    replace(self.result, hits=(replace(self.hit,url="https://127.0.0.1/x"),))):
            _, _, job = self.fixture()
            with self.assertRaises(Refused):
                job.complete(bad)
            with self.assertRaises(Refused):
                job.result()
            with self.assertRaises(Refused):
                job.complete(self.result)

    def test_query_and_display_invalid_text(self):
        for text in (" ", "x"*513, "a\x00b", "a\u202eb", "a\ud800b"):
            with self.assertRaises(Refused):
                replace(self.query, text=text).call()

    def test_no_hits_is_not_failure_and_revocation_refuses_publication(self):
        g, _, job = self.fixture()
        empty = replace(self.result, hits=())
        job.complete(empty)
        self.assertEqual(job.result()[1].hits, ())
        g.revoke("search")
        with self.assertRaises(Refused):
            job.result()
        _, _, job = self.fixture()
        job.cancel()
        with self.assertRaises(Refused):
            job.complete(self.result)
