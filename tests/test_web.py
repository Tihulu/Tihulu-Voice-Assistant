# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Approved-target tests: no page is fetched and no script is run."""
import unittest

from tihulu_jarvis.voice.m1 import Hold
from tihulu_jarvis.web import (MAX_TARGETS, Target, forget, parse, plan,
                               register, text_preview)


class TargetTests(unittest.TestCase):
    def test_a_target_names_its_host_and_path_only(self):
        target = parse("https://example.test/docs/index")
        self.assertEqual((target.host, target.path, target.submit),
                         ("example.test", "/docs/index", False))

    def test_only_a_public_https_target_is_registered(self):
        for raw in ("http://example.test/x", "https://localhost/x",
                    "https://127.0.0.1/x", "https://10.0.0.5/x",
                    "https://example.test/x?q=1", "https://user@example.test/x",
                    "https://example.test/../etc", "https://example.test",
                    "not-a-url", "https://exa mple.test/x"):
            with self.subTest(raw=raw):
                with self.assertRaises(Hold):
                    parse(raw)

    def test_a_submit_target_is_a_separate_right(self):
        approved = parse("https://example.test/form/submit")
        self.assertTrue(approved.submit)
        plain = parse("https://example.test/form")
        self.assertFalse(plain.submit)


class PlanTests(unittest.TestCase):
    def test_reading_a_registered_target_is_a_get(self):
        target = parse("https://example.test/docs")
        self.assertEqual(plan(target), ("GET", "https://example.test/docs", "navigate_only"))

    def test_submitting_needs_the_separate_right(self):
        with self.assertRaisesRegex(Hold, "submission_not_approved"):
            plan(parse("https://example.test/form"), submit=True)
        approved = parse("https://example.test/form/submit")
        self.assertEqual(plan(approved, submit=True),
                         ("POST", "https://example.test/form/submit", "submission_approved"))


class RegistryTests(unittest.TestCase):
    def test_targets_are_deduplicated_and_bounded(self):
        target = parse("https://example.test/docs")
        targets = register((), target)
        self.assertEqual(register(targets, target), (target,))
        other = register(targets, parse("https://other.test/"))
        self.assertEqual(len(other), 2)
        with self.assertRaisesRegex(Hold, "too_many_targets"):
            register(tuple(Target("host-%d.test" % index) for index in range(MAX_TARGETS)),
                     Target("overflow.test"))

    def test_forgetting_a_target_is_not_an_error(self):
        target = parse("https://example.test/docs")
        self.assertEqual(forget((target,), target), ())
        self.assertEqual(forget((), target), ())


class PreviewTests(unittest.TestCase):
    def test_a_page_is_readable_text_without_its_scripts(self):
        body = ("<html><head><script>alert('x')</script><style>p{color:red}</style></head>"
                "<body><p>Merhaba&nbsp;dunya</p><a href='/x'>baglanti</a></body></html>")
        out = text_preview(body)
        self.assertIn("Merhaba", out)
        self.assertIn("dunya", out)
        self.assertNotIn("alert", out)
        self.assertNotIn("color", out)
        self.assertNotIn("<", out)

    def test_a_long_page_is_cut_and_says_so(self):
        out = text_preview("<p>" + "x" * 5000 + "</p>")
        self.assertTrue(out.endswith(" …"))
        # The cut is stated with one ellipsis, so a reader can tell.
        self.assertLessEqual(len(out), 4002)
        self.assertIn(" …", out)

    def test_an_oversized_page_is_refused_before_reading_it(self):
        with self.assertRaisesRegex(Hold, "page_too_large"):
            text_preview("<p>" + "x" * 300000 + "</p>")
        with self.assertRaises(Hold):
            text_preview(b"bytes are not a page")
