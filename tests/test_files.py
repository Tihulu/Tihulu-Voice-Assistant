# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Local folder tests: only temporary directories are touched."""
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.files import (MAX_ENTRIES, checked_root, inside, list_dir,
                                 preview)
from tihulu_jarvis.voice.m1 import Hold


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = str(self.tmp)
        (self.tmp / "notes.txt").write_text("Benim kelimem mavi.\n", encoding="utf-8")
        (self.tmp / "pictures").mkdir()
        (self.tmp / "pictures" / "one.txt").write_text("birer", encoding="utf-8")

    def test_a_listing_is_bounded_sorted_and_sized(self):
        folder = list_dir(self.root)
        # Folders come first, then files, each alphabetically.
        names = [entry.name for entry in folder.entries]
        self.assertEqual(names, ["pictures", "notes.txt"])
        by_name = {entry.name: entry for entry in folder.entries}
        self.assertTrue(by_name["pictures"].directory)
        self.assertFalse(by_name["notes.txt"].directory)
        self.assertEqual(by_name["notes.txt"].size, 20)

    def test_a_missing_or_relative_root_is_refused(self):
        # A root is the owner's own choice, so it may sit anywhere absolute;
        # only a relative or non-string path is refused.
        for bad in ("", None, 42, "relative/path"):
            with self.subTest(root=bad):
                with self.assertRaises(Hold):
                    checked_root(bad)
        with self.assertRaisesRegex(Hold, "folder_not_found"):
            list_dir(str(self.tmp / "missing"))

    def test_the_listing_is_bounded(self):
        for index in range(MAX_ENTRIES + 20):
            (self.tmp / ("f%03d.txt" % index)).write_text("x", encoding="utf-8")
        self.assertEqual(len(list_dir(self.root).entries), MAX_ENTRIES)


class InsideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = str(self.tmp)

    def test_only_a_name_inside_the_root_can_be_addressed(self):
        self.assertEqual(inside(self.root, "notes.txt").name, "notes.txt")
        for bad in ("", None, "..", "../etc/passwd", "/etc/passwd", "a/b", "a\\b"):
            with self.subTest(name=bad):
                with self.assertRaises(Hold):
                    inside(self.root, bad)

    def test_a_symlink_out_of_the_root_is_refused(self):
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_text("dısarı", encoding="utf-8")
        link = self.tmp / "link.txt"
        link.symlink_to(outside)
        with self.assertRaisesRegex(Hold, "path_outside_root"):
            inside(self.root, "link.txt")


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = str(self.tmp)

    def test_a_text_file_is_previewed_with_its_own_text(self):
        (self.tmp / "notes.txt").write_text("Benim kelimem mavi.\n", encoding="utf-8")
        self.assertEqual(preview(self.root, "notes.txt"), "Benim kelimem mavi.")

    def test_a_long_file_is_cut_and_says_so(self):
        (self.tmp / "long.txt").write_text("x" * 9000, encoding="utf-8")
        out = preview(self.root, "long.txt")
        self.assertTrue(out.endswith(" …"))
        self.assertLessEqual(len(out), 8002)

    def test_binary_empty_and_missing_files_are_refused(self):
        (self.tmp / "image.bin").write_bytes(b"\x89PNG\x00\x01")
        with self.assertRaisesRegex(Hold, "file_not_text"):
            preview(self.root, "image.bin")
        (self.tmp / "blank.txt").write_text("   \n", encoding="utf-8")
        with self.assertRaisesRegex(Hold, "file_empty"):
            preview(self.root, "blank.txt")
        with self.assertRaisesRegex(Hold, "file_not_found"):
            preview(self.root, "missing.txt")
        with self.assertRaisesRegex(Hold, "file_not_found"):
            preview(self.root, "pictures")  # a directory is not a file
