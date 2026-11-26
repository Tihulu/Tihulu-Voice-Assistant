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
