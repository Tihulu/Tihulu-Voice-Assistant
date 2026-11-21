# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Attachment tests: only temporary files in a private directory are read."""
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.attachments import (MAX_BYTES, MAX_CHARS, describe, load,
                                       prompt_block)
from tihulu_jarvis.voice.m1 import Hold


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "notes.txt"
        self.path.write_text("Benim kelimem mavi.\n", encoding="utf-8")

    def test_a_text_file_is_read_and_named_only(self):
        item = load(str(self.path))
        self.assertEqual((item.name, item.chars, item.truncated), ("notes.txt", 19, False))
        self.assertIn("mavi", item.text)
        # The directory never enters the attachment.
        self.assertNotIn(str(self.tmp), repr(item))

    def test_bounded_by_size_and_characters(self):
        big = self.tmp / "big.txt"
        big.write_text("a" * (MAX_BYTES + 1), encoding="utf-8")
        with self.assertRaisesRegex(Hold, "attachment_too_large"):
            load(str(big))
        long = self.tmp / "long.txt"
        long.write_text("b" * (MAX_CHARS + 50), encoding="utf-8")
        item = load(str(long))
        self.assertTrue(item.truncated)
        self.assertEqual(item.chars, MAX_CHARS)
        self.assertEqual(item.size, MAX_CHARS + 50)

    def test_binary_and_empty_files_are_refused(self):
        binary = self.tmp / "image.bin"
        binary.write_bytes(b"\x89PNG\x00\x01rest")
        with self.assertRaisesRegex(Hold, "attachment_not_text"):
            load(str(binary))
        blank = self.tmp / "blank.txt"
        blank.write_text("   \n\t\n", encoding="utf-8")
        with self.assertRaisesRegex(Hold, "attachment_empty"):
            load(str(blank))
        wrong = self.tmp / "latin.txt"
        wrong.write_bytes(b"\xff\xfe not utf-8")
        with self.assertRaisesRegex(Hold, "attachment_not_text"):
            load(str(wrong))

    def test_a_missing_or_relative_file_is_not_read(self):
        for bad in ("", None, 42, self.tmp.name, str(self.tmp / "missing.txt"),
                    str(self.tmp)):
            with self.subTest(path=bad):
                with self.assertRaises(Hold):
                    load(bad)

    def test_a_directory_is_not_a_file(self):
        with self.assertRaisesRegex(Hold, "attachment_not_found"):
            load(str(self.tmp))


class DescribeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "notes.txt"
        self.path.write_text("Benim kelimem mavi.\n", encoding="utf-8")

    def test_the_line_states_length_and_truncation(self):
        item = load(str(self.path))
        self.assertEqual(describe(item), "notes.txt · 19 characters")
        long_path = self.tmp / "long.txt"
        long_path.write_text("c" * (MAX_CHARS + 10), encoding="utf-8")
        self.assertIn("truncated", describe(load(str(long_path))))

    def test_the_block_is_delimited_and_pathless(self):
        item = load(str(self.path))
        block = prompt_block(item)
        self.assertTrue(block.startswith("[attached file: notes.txt,"))
        self.assertTrue(block.endswith("[end of attached file]"))
        self.assertIn("mavi", block)
        self.assertNotIn(str(self.tmp), block)
