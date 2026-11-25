import unittest
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from research import canonical
from research_transport import addresses, fixture_response

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("r2", "session", "profile", 1, "route", "LOCAL_ONLY", False, True)
        self.url = "https://example.com/research"
        target = canonical(self.url).identity
        self.g = FixtureGateway(self.ctx, (profiles.Rule("external_network", (target,), "allow"),),
                                clock=profiles.PolicyClock())
        self.grant = self.g.fixture_grant("r", Request(self.ctx, "research", target, "local", "public"), 60)
        self.headers = (("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", "4"))

    def response(self, headers=None, body=b"mavi", ips=("8.8.8.8",), peer="8.8.8.8"):
        return fixture_response(self.g, self.grant, self.ctx, self.url, self.url, ips, peer,
                                self.headers if headers is None else headers, body)

    def test_mixed_address_sets_fail_closed(self):
        for ip in ("127.0.0.1", "100.64.0.1", "169.254.169.254", "224.0.0.1",
                   "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:0808:0808::1", "fe80::1"):
            with self.subTest(ip=ip), self.assertRaises(Refused):
                addresses(("8.8.8.8", ip))

    def test_peer_must_match_approved_fixture_set(self):
        with self.assertRaises(Refused):
            self.response(peer="1.1.1.1")
        self.assertEqual(self.response().text, "mavi")

    def test_ambiguous_or_encoded_response_refused(self):
        for h in (self.headers + (("content-length", "4"),),
                  self.headers + (("Transfer-Encoding", "chunked"),),
                  self.headers + (("Content-Encoding", "gzip"),),
                  self.headers + (("Location", "https://other.com/"),)):
            with self.assertRaises(Refused):
                self.response(headers=h)

    def test_truncation_utf8_and_revocation(self):
        for body in (b"x", b"\xffxxx", b"x"*65537):
            with self.assertRaises(Refused):
                self.response(body=body)
        self.g.revoke("r")
        with self.assertRaises(Refused):
            self.response()
