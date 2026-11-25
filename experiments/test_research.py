import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from research import canonical, scoped, snapshot, citation

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("r2", "session", "profile", 1, "route", "LOCAL_ONLY", False, True)
        self.url = "https://example.com/research/item"
        self.target = canonical(self.url)
        self.g = FixtureGateway(self.ctx, (profiles.Rule("external_network", (self.target.identity,), "allow"),),
                                clock=profiles.PolicyClock())
        self.grant = self.g.fixture_grant("research", Request(self.ctx, "research", self.target.identity, "local", "public"), 60)

    def test_ambiguous_urls_rejected(self):
        for url in ("http://example.com/", "https://example.com:443/", "https://user@example.com/",
                    "https://example.com/%2f", "https://example.com/a/../b", "https://example.com/a?x=1",
                    "https://example.com/a#x", "https://example.com//a", "https://127.0.0.1/",
                    "https://0x7f000001/", "https://xn--foo.com/", "https://foo.local/",
                    "https://example.com/\nfoo", "https://example.com/\\foo"):
            with self.subTest(url=url), self.assertRaises(Refused):
                canonical(url)

    def test_exact_segments_and_host(self):
        root = canonical("https://example.com/research")
        self.assertTrue(scoped(self.target, root))
        self.assertFalse(scoped(canonical("https://example.com/research2"), root))
        self.assertFalse(scoped(canonical("https://example.com.evil.com/research"), root))

    def test_citation_uses_immutable_digest_and_codepoints(self):
        source = snapshot(self.g, self.grant, self.ctx, self.url, "https://example.com/research",
                          "Fixture", "mavi ü")
        self.assertEqual(citation(source, source.digest, 5, 6), "ü")
        for candidate, start, end in ((replace(source, text="changed"), 0, 2), (source, True, 2),
                                      (source, -1, 2), (source, 0, 100)):
            with self.assertRaises(Refused):
                citation(candidate, source.digest, start, end)

    def test_source_rejected_after_revoke(self):
        self.g.revoke("research")
        with self.assertRaises(Refused):
            snapshot(self.g, self.grant, self.ctx, self.url, "https://example.com/research", "Title", "text")

    def test_scope_and_text_bounds(self):
        for root, text in (("https://example.com/other", "text"),
                           ("https://example.com/research", "x" * 65537),
                           ("https://example.com/research", "a\u202eb")):
            with self.assertRaises(Refused):
                snapshot(self.g, self.grant, self.ctx, self.url, root, "Title", text)
