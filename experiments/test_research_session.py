import unittest
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from research import canonical
from research_session import ResearchSession

class ResearchSessionTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task", "session", "profile", 1, "route", "LOCAL_ONLY", False, True)
        self.g = FixtureGateway(self.ctx, (profiles.Rule("external_network", (), "allow"),), clock=profiles.PolicyClock())
        self.s = ResearchSession(self.g, self.ctx, "https://example.com/research")
    def add(self, n, body=b"mavi"):
        url = "https://example.com/research/" + str(n)
        identity = canonical(url).identity
        grant = self.g.fixture_grant("g"+str(n), Request(self.ctx,"research",identity,"local","public"),60)
        result = self.s.collect(grant,url,("8.8.8.8",),"8.8.8.8",
                               (("Content-Type","text/plain; charset=utf-8"),("Content-Length",str(len(body)))),body)
        return result
    def test_citation_revocation_and_close(self):
        a = self.add(1)
        b = self.add(2,b"yes")
        self.assertEqual(self.s.cite(a,sha256(b"mavi").hexdigest(),0,4).quote,"mavi")
        self.g.revoke("g1")
        with self.assertRaises(Refused):
            self.s.cite(a,sha256(b"mavi").hexdigest(),0,4)
        self.assertEqual(self.s.cite(b,sha256(b"yes").hexdigest(),0,3).quote,"yes")
        self.s.close()
        with self.assertRaises(Refused):
            self.s.cite(b,sha256(b"yes").hexdigest(),0,3)
    def test_total_source_limit(self):
        for n in range(8):
            self.add(n)
        with self.assertRaises(Refused):
            self.add(8)
    def test_total_bytes_limit(self):
        for n in range(4):
            self.add(n,b"x"*65536)
        with self.assertRaises(Refused):
            self.add(4)

    def test_close_during_collect_cannot_restore_source(self):
        from unittest.mock import patch
        from research_session import fixture_response as original
        def close(*args):
            result=original(*args)
            self.s.close()
            return result
        with patch("research_session.fixture_response",side_effect=close):
            with self.assertRaisesRegex(Refused,"session_closed"):
                self.add(1)
        self.assertEqual(self.s._sources,{})
        self.assertEqual(self.s._bytes,0)

    def test_close_during_citation_cannot_return_quote(self):
        from unittest.mock import patch
        from research_session import citation as original
        a=self.add(1)
        def close(*args):
            quote=original(*args)
            self.s.close()
            return quote
        with patch("research_session.citation",side_effect=close):
            with self.assertRaisesRegex(Refused,"session_closed"):
                self.s.cite(a,sha256(b"mavi").hexdigest(),0,4)
