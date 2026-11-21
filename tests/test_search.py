# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Conversation search tests: only local data structures are used."""
import unittest

from tihulu_jarvis.search import MAX_QUERY, MAX_RESULTS, count, find, snippet
from tihulu_jarvis.voice.m1 import Hold, Message


def history(*items):
    return tuple(Message("user" if index % 2 == 0 else "assistant", item)
                 for index, item in enumerate(items))


class FindTests(unittest.TestCase):
    def test_matches_are_newest_first_and_carry_the_index(self):
        rows = find(history("what is my word", "your word is mavi", "thanks"), "word")
        self.assertEqual([row[0] for row in rows], [1, 0])
        self.assertEqual([row[1] for row in rows], ["assistant", "user"])

    def test_a_search_is_a_substring_not_a_pattern(self):
        rows = history("what is my word")
        # Pattern metacharacters are ordinary letters here: they match only
        # themselves, so a search can never become an expression engine.
        self.assertEqual(find(rows, ".*"), ())
        self.assertEqual(find(rows, "(unclosed"), ())
        self.assertEqual(find(rows, "w.rd"), ())
        self.assertEqual(find(rows, "my word")[0][2], "what is my word")

    def test_a_query_is_bounded(self):
        for bad in ("", "   ", "x" * (MAX_QUERY + 1), None, 42):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    find(history("what is my word"), bad)
        for bad in (0, -1, MAX_RESULTS + 1, "3", None):
            with self.subTest(limit=bad):
                with self.assertRaises(Hold):
                    find(history("what is my word"), "word", bad)

    def test_results_are_limited_and_boundaries_are_clamped(self):
        many = history(*["word %d" % index for index in range(80)])
        rows = find(many, "word")
        self.assertEqual(len(rows), MAX_RESULTS)
        self.assertEqual(rows[0][0], 79)

    def test_unknown_entries_are_skipped_not_guessed_at(self):
        mixed = history("my word is mavi") + ((None, "no match"), (42, None), "not a message")
        self.assertEqual(len(find(mixed, "word")), 1)

    def test_case_and_turkish_characters_match_without_the_locale(self):
        rows = find(history("KELİMEM MAVİ", "İyiyim"), "kelimem")
        self.assertEqual(len(rows), 1)
        self.assertEqual(find(history("İyiyim"), "iyiyim")[0][1], "user")


class SnippetTests(unittest.TestCase):
    def test_a_short_message_is_returned_whole(self):
        self.assertEqual(snippet("my word is mavi", "word"), "my word is mavi")

    def test_a_long_message_windows_around_the_match(self):
        body = "x" * 40 + "needle" + "y" * 40
        out = snippet(body, "needle", 30)
        self.assertIn("needle", out)
        self.assertTrue(out.startswith("…"))
        self.assertTrue(out.endswith("…"))
        self.assertLess(len(out), len(body))
        # A match that reaches the end of the message is marked once, at the
        # start: the ellipses describe the window, never the content.
        self.assertEqual(snippet("y" * 200 + "needle", "needle", 30).count("…"), 1)

    def test_no_match_means_no_snippet(self):
        self.assertEqual(snippet("nothing here", "absent"), "")


class CountTests(unittest.TestCase):
    def test_the_count_is_the_number_of_matches(self):
        rows = history("word", "other", "word again")
        self.assertEqual(count(rows, "word"), 2)
        self.assertEqual(count(rows, "absent"), 0)
