# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Catalog fetch policy tests. The opener is injected; no socket is opened."""
import json
import unittest

from tihulu_jarvis import catalog_fetch
from tihulu_jarvis.voice.m1 import Hold

ALLOWED = ("catalog.example.test", "mirror.example.test")


class PolicyTests(unittest.TestCase):
    def test_an_allowed_https_source_is_accepted(self):
        for url in ("https://catalog.example.test/list.json",
                    "https://mirror.example.test/a/b/list.json"):
            with self.subTest(url=url):
                catalog_fetch.allowed(url, ALLOWED)

    def test_only_https_from_allowed_hosts_is_accepted(self):
        for bad, reason in (
                ("http://catalog.example.test/l.json", "catalog_source_not_https"),
                ("https://other.example.test/l.json", "catalog_host_not_allowed"),
                ("https://127.0.0.1/l.json", "catalog_source_not_public"),
                ("https://10.0.0.5/l.json", "catalog_source_not_public"),
                ("https://192.168.1.5/l.json", "catalog_source_not_public"),
                ("https://catalog.example.test/l.json?token=x", "invalid_local_settings"),
                ("https://catalog.example.test/l.json#frag", "invalid_local_settings"),
                ("https://user:pw@catalog.example.test/l.json", "invalid_local_settings"),
                ("https://catalog.example.test/../etc/passwd", "invalid_local_settings"),
                ("https://catalog.example.test/sp ace", "invalid_local_settings"),
                ("", "catalog_source_not_https"),
                (None, "invalid_local_settings")):
            with self.subTest(url=bad):
                with self.assertRaisesRegex(Hold, reason):
                    catalog_fetch.allowed(bad, ALLOWED)

    def test_bodies_are_bounded_and_must_parse(self):
        document = json.dumps({"source_name": "x", "entries": []})
        self.assertEqual(catalog_fetch.verify_body(document), document)
        with self.assertRaisesRegex(Hold, "invalid_catalog"):
            catalog_fetch.verify_body("not json")
        with self.assertRaisesRegex(Hold, "invalid_catalog"):
            catalog_fetch.verify_body(json.dumps([1, 2]))
        with self.assertRaisesRegex(Hold, "catalog_too_large"):
            catalog_fetch.verify_body("[" + "x" * (catalog_fetch.MAX_BYTES + 1) + "]")



class FetchTests(unittest.IsolatedAsyncioTestCase):
    URL = "https://catalog.example.test/list.json"

    async def fetch(self, body="[]", **kw):
        async def opener(url, timeout):
            return body
        return await catalog_fetch.fetch(self.URL, ALLOWED, opener, **kw)

    async def test_an_allowed_source_is_fetched_and_verified(self):
        document = json.dumps({"source_name": "x", "entries": []})
        self.assertEqual(await self.fetch(document), document)

    async def test_a_failing_transport_is_a_bounded_reason(self):
        async def broken(url, timeout):
            raise OSError("fixture")
        with self.assertRaisesRegex(Hold, "catalog_fetch_failed"):
            await catalog_fetch.fetch(self.URL, ALLOWED, broken)

    async def test_a_rejected_source_is_never_fetched(self):
        calls = []

        async def opener(url, timeout):
            calls.append(url)
            return "[]"
        with self.assertRaisesRegex(Hold, "catalog_host_not_allowed"):
            await catalog_fetch.fetch("https://other.example.test/l.json", ALLOWED, opener)
        self.assertEqual(calls, [])

    async def test_a_missing_transport_is_honest(self):
        with self.assertRaisesRegex(Hold, "transport_not_available"):
            await catalog_fetch.fetch(self.URL, ALLOWED, None)

    async def test_the_default_opener_declares_itself_unavailable(self):
        # It exists so the shape is explicit; the production path is a gate.
        with self.assertRaisesRegex(Hold, "transport_not_available"):
            catalog_fetch.default_opener()("https://catalog.example.test/l.json", 1)

    def test_the_stdlib_opener_verifies_tls_by_default(self):
        import inspect
        source = inspect.getsource(catalog_fetch.urlopen_opener)
        self.assertIn("ssl.create_default_context()", source)
        self.assertIn("response.headers.get(\"Location\")", source)
