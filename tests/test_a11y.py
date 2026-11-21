# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Display-preference tests: no window is created."""
import unittest

from tihulu_jarvis.a11y import SCALES, checked, css, motion_words
from tihulu_jarvis.voice.m1 import Hold


class ScaleTests(unittest.TestCase):
    def test_a_stored_scale_is_one_of_the_two(self):
        self.assertEqual(SCALES, ("normal", "large"))
        for value in ("normal", "large", None, ""):
            with self.subTest(value=value):
                self.assertEqual(checked(value), value or "normal")
        for bad in ("huge", "small", "NORMAL", 12, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    checked(bad)

    def test_the_stylesheet_grows_with_the_scale(self):
        normal = css("normal")
        large = css("large")
        self.assertIn("font-size: 11pt", normal)
        self.assertIn("font-size: 14pt", large)
        self.assertNotIn("11pt", large.replace("14pt", ""))

    def test_no_scale_introduces_motion(self):
        for scale in (None, "", *SCALES):
            with self.subTest(scale=scale):
                self.assertEqual(motion_words(css(scale)), ())

    def test_the_base_look_is_preserved(self):
        base = ".hero { font-size: 24pt; font-weight: bold; }"
        out = css("large", base)
        self.assertIn(".hero", out)
        self.assertIn("font-size: 14pt", out)
