# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused, ACTIONS
from capabilities import CapabilityCall, simulate


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task", "session", "profile", 1, "route", "SECURE_HYBRID", False)
        self.ocr = CapabilityCall("tesseract", "5.5", "ocr_image",
                                  (("artifact", "image1"), ("digest", "a"*64), ("language", "tr")))
        self.browser = CapabilityCall("firefox", "fixture", "browser_read",
                                      (("origin", "example.test"), ("tab", "tab1")))
        self.pdf = CapabilityCall("poppler", "fixture", "pdf_extract",
                                  (("artifact", "pdf1"), ("digest", "b"*64), ("page", 1)))
        self.search = CapabilityCall("search_provider", "fixture", "web_search",
                                     (("query_digest", "c"*64),))
        self.app = CapabilityCall("libreoffice", "fixture", "window_inspect", (("window", "win1"),))
        self.engine = CapabilityCall("ffmpeg", "fixture", "audio_convert",
                                     (("artifact", "clip"), ("digest", "d"*64), ("output", "new")))
        self.export = CapabilityCall("pdf_engine", "fixture", "pdf_export",
                                     (("artifact", "pdf1"), ("digest", "b"*64), ("output", "newpdf")))

    def gateway(self, call, ctx=None, actions=None):
        ctx = ctx or self.ctx
        req = call.request(ctx)
        rules = tuple(profiles.Rule(a, (req.target,), "allow")
                      for a in (set(ACTIONS.values()) if actions is None else actions))
        return FixtureGateway(ctx, rules, clock=profiles.PolicyClock())

    def test_all_closed_capabilities_issue_only_synthetic_receipt(self):
        for call in (self.ocr, self.browser, self.pdf, self.search, self.app, self.engine, self.export,
                     replace(self.browser, adapter="brave")):
            with self.subTest(call=call):
                g = self.gateway(call)
                grant = g.fixture_grant("one", call.request(self.ctx), 30)
                receipt = simulate(g, grant, self.ctx, call)
                self.assertEqual(receipt, ("SIMULATED_ONLY", call.adapter, call.operation,
                                           call.request(self.ctx).target))
                with self.assertRaisesRegex(Refused, "grant_already_claimed"):
                    simulate(g, grant, self.ctx, call)

    def test_no_cross_browser_version_window_page_or_input_replay(self):
        for original, changed in (
            (self.browser, replace(self.browser, adapter="brave")),
            (self.browser, replace(self.browser, version="other")),
            (self.browser, replace(self.browser, args=(("origin", "other.test"), ("tab", "tab1")))),
            (self.app, replace(self.app, args=(("window", "win2"),))),
            (self.pdf, replace(self.pdf, args=(("artifact", "pdf1"), ("digest", "b"*64), ("page", 2)))),
            (self.ocr, replace(self.ocr, args=(("artifact", "image1"), ("digest", "d"*64), ("language", "tr")))),
        ):
            g = self.gateway(original)
            grant = g.fixture_grant("one", original.request(self.ctx), 30)
            with self.assertRaisesRegex(Refused, "request_mismatch"):
                simulate(g, grant, self.ctx, changed)
            self.assertEqual(simulate(g, grant, self.ctx, original)[0], "SIMULATED_ONLY")

    def test_all_host_capabilities_deny_cloud_actor_and_cloud_mode(self):
        for call in (self.ocr, self.browser, self.pdf, self.app, self.engine, self.export):
            g = self.gateway(call)
            with self.assertRaises(Refused):
                g.fixture_grant("cloud", call.request(self.ctx, actor="cloud"), 30)
            ctx = replace(self.ctx, mode="CLOUD_CHAT")
            with self.assertRaises(Refused):
                self.gateway(call, ctx).fixture_grant("host", call.request(ctx), 30)

    def test_offline_denies_search_and_browser_not_local_ocr(self):
        ctx = replace(self.ctx, mode="LOCAL_ONLY", offline=True)
        for call in (self.search, self.browser):
            with self.assertRaisesRegex(Refused, "offline"):
                self.gateway(call, ctx).fixture_grant("one", call.request(ctx), 30)
        g = self.gateway(self.ocr, ctx)
        grant = g.fixture_grant("one", self.ocr.request(ctx), 30)
        self.assertEqual(simulate(g, grant, ctx, self.ocr)[0], "SIMULATED_ONLY")

    def test_combined_policy_ceilings_are_not_interchangeable(self):
        for call, actions in (
            (self.ocr, ("filesystem_read", "host_shell")),
            (self.export, ("filesystem_read", "filesystem_write", "host_shell")),
            (self.browser, ("browser_navigate", "external_network", "host_shell")),
            (self.engine, ("filesystem_read", "filesystem_write", "host_shell")),
        ):
            for missing in actions:
                g = self.gateway(call, actions=set(actions)-{missing})
                with self.assertRaises(Refused):
                    g.fixture_grant("one", call.request(self.ctx), 30)

    def test_untyped_operations_and_source_overwrite_refused(self):
        for call in (
            replace(self.browser, operation="submit"),
            replace(self.app, operation="type_password"),
            replace(self.engine, operation="run_shell"),
            replace(self.ocr, args=(("artifact", "/tmp/input"), ("digest", "a"*64), ("language", "tr"))),
            replace(self.pdf, args=(("artifact", "pdf1"), ("digest", "b"*64), ("page", True))),
            replace(self.export, args=(("artifact", "pdf1"), ("digest", "b"*64), ("output", "pdf1"))),
        ):
            with self.subTest(call=call), self.assertRaises(Refused):
                call.request(self.ctx)

    def test_revocation_after_claim_prevents_receipt(self):
        g = self.gateway(self.ocr)
        grant = g.fixture_grant("one", self.ocr.request(self.ctx), 30)
        claim = g.claim
        def revoke(given, req):
            claim(given, req)
            g.revoke(given.identity)
        with patch.object(g, "claim", side_effect=revoke), self.assertRaises(Refused):
            simulate(g, grant, self.ctx, self.ocr)


    def test_browser_navigation_snapshot_require_exact_profile_page_and_content(self):
        nav = CapabilityCall("firefox", "fixture", "navigate",
                             (("profile", "owned1"), ("tab", "tab1"),
                              ("url", "https://example.org/docs")))
        g = self.gateway(nav)
        grant = g.fixture_grant("one", nav.request(self.ctx), 60)
        for changed in (
            replace(nav, adapter="brave"),
            replace(nav, args=(("profile", "owned2"), ("tab", "tab1"), nav.args[2])),
            replace(nav, args=(nav.args[0], nav.args[1], ("url", "https://example.org/other"))),
            replace(nav, operation="snapshot", args=nav.args+(("digest", "a"*64),)),
        ):
            with self.assertRaisesRegex(Refused, "request_mismatch"):
                simulate(g, grant, self.ctx, changed)
        self.assertEqual(simulate(g, grant, self.ctx, nav)[0], "SIMULATED_ONLY")

    def test_browser_rejects_ambiguous_and_private_authorities_before_grant(self):
        for raw in ("http://example.org/x", "https://127.0.0.1/x",
                    "https://localhost.local/x", "https://example.org:443/x",
                    "https://user@example.org/x", "https://example.org/../x",
                    "https://example.org/%2e%2e/x", "https://example.org/x?q=1",
                    "https://example.org/x#anchor", "https://example.org./x",
                    "https://example.org//x"):
            call = CapabilityCall("brave", "fixture", "navigate",
                                  (("profile", "owned"), ("tab", "tab1"), ("url", raw)))
            with self.subTest(raw=raw), self.assertRaises(Refused):
                call.request(self.ctx)
        for host in ("127.0.0.1", "127.1", "100.64.0.1", "-bad.org"):
            call = replace(self.browser, args=(("origin", host), ("tab", "tab1")))
            with self.assertRaises(Refused):
                call.request(self.ctx)

    def test_capability_identity_rejects_str_subclass_and_noncanonical_args(self):
        class Fake(str):
            pass
        for call in (replace(self.ocr, adapter=Fake("tesseract")),
                     replace(self.ocr, args=((Fake("artifact"), "image1"), *self.ocr.args[1:])),
                     replace(self.ocr, args=(("artifact", Fake("image1")), *self.ocr.args[1:]))):
            with self.assertRaises(Refused):
                call.request(self.ctx)
