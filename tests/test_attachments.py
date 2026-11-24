# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Attachment tests: only temporary files in a private directory are read."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


@unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor reader")
class BoundedAttachmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "notes.txt"
        self.path.write_bytes(b"synthetic note")

    def test_empty_file_keeps_legacy_reason(self):
        self.path.write_bytes(b"")
        with self.assertRaisesRegex(Hold, "^attachment_empty$"):
            load(str(self.path))

    def test_symlink_file_directory_and_fifo_are_refused(self):
        link = self.root / "link.txt"
        link.symlink_to(self.path)
        real = self.root / "real"
        real.mkdir()
        (real / "notes.txt").write_bytes(b"synthetic note")
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        fifo = self.root / "pipe.txt"
        os.mkfifo(fifo)
        for path in (link, alias / "notes.txt", fifo):
            with self.subTest(path=path):
                with patch("os.read", side_effect=AssertionError("unexpected data read")):
                    with self.assertRaises(Hold):
                        load(str(path))

    def test_no_io_if_unauthorized(self):
        with patch("os.open", side_effect=AssertionError("unexpected open")):
            with self.assertRaisesRegex(Hold, "^attachment_not_authorized$"):
                load(str(self.path), authorized=lambda: False)

    def test_revocation_during_read(self):
        allowed = True
        original = os.read
        def revoke(fd, n):
            nonlocal allowed
            data = original(fd, n)
            allowed = False
            return data
        with patch("os.read", side_effect=revoke):
            with self.assertRaisesRegex(Hold, "^attachment_not_authorized$"):
                load(str(self.path), authorized=lambda: allowed)

    def test_detectable_mutation_never_publishes_snapshot(self):
        original = os.read
        def mutate(fd, n):
            data = original(fd, n)
            if data:
                self.path.write_bytes(b"changed")
            return data
        with patch("os.read", side_effect=mutate):
            with self.assertRaisesRegex(Hold, "^attachment_changed_during_read$"):
                load(str(self.path))

    def test_oversized_growth_during_read_is_bounded(self):
        original = os.read
        def grow(fd, n):
            data = original(fd, n)
            if data and self.path.stat().st_size < MAX_BYTES:
                with self.path.open("ab") as stream:
                    stream.write(b"x" * MAX_BYTES)
            return data
        with patch("os.read", side_effect=grow):
            with self.assertRaisesRegex(Hold, "^attachment_too_large$"):
                load(str(self.path))


    def test_replacement_after_picker_identity_is_refused_before_read(self):
        from tihulu_jarvis.selected_csv import selection_identity
        identity = selection_identity(str(self.path), authorized=lambda: True,
                                      allow_empty=True)
        self.path.rename(self.root / "old.txt")
        self.path.write_bytes(b"replacement")
        with patch("os.read", side_effect=AssertionError("replacement read")):
            with self.assertRaisesRegex(Hold, "^attachment_selection_changed$"):
                load(str(self.path), expected_identity=identity)
