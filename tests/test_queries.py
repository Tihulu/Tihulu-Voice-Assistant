# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Search-history tests: only local data structures are used."""
import unittest

from tihulu_jarvis.queries import MAX_QUERIES, checked, clear, forget, remember
from tihulu_jarvis.voice.m1 import Hold


class HistoryTests(unittest.TestCase):
    def test_a_query_becomes_the_newest_without_a_duplicate(self):
        history = remember((), "kelimem")
        self.assertEqual(history, ("kelimem",))
        history = remember(history, "mavi")
        self.assertEqual(history, ("mavi", "kelimem"))
        # Re-asking the oldest moves it to the front instead of repeating it.
        self.assertEqual(remember(history, "kelimem"), ("kelimem", "mavi"))

    def test_the_history_is_bounded(self):
        history = ()
        for index in range(MAX_QUERIES + 10):
            history = remember(history, "query-%d" % index)
        self.assertEqual(len(history), MAX_QUERIES)
        self.assertEqual(history[0], "query-%d" % (MAX_QUERIES + 9))
        self.assertEqual(history[-1], "query-10")

    def test_a_bad_query_is_refused(self):
        for bad in ("", "   ", "x" * 121, None, 7, []):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    remember(("kelimem",), bad)

    def test_a_bad_stored_history_is_refused_not_guessed_at(self):
        for bad in (("kelimem", "x" * 121), ("kelimem", 5), "kelimem", None, [""]):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    checked(bad)

    def test_forgetting_and_clearing_leave_nothing_behind(self):
        history = remember(remember((), "kelimem"), "mavi")
        self.assertEqual(forget(history, "kelimem"), ("mavi",))
        self.assertEqual(forget(history, "absent"), history)
        self.assertEqual(clear(), ())
