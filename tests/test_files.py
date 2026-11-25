# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Local folder tests: only temporary directories are touched."""
import os
import tempfile
import unittest
from unittest.mock import patch
from tihulu_jarvis import files
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


class SafeFolderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / "selected"
        self.folder.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("SECRET")

    def test_links_special_files_and_linked_root_ancestors_are_refused(self):
        (self.folder / "escape.txt").symlink_to(self.outside / "secret.txt")
        os.mkfifo(self.folder / "pipe.txt")
        self.assertEqual(list_dir(str(self.folder)).entries, ())
        for name in ("escape.txt", "pipe.txt"):
            with self.assertRaises(Hold):
                preview(str(self.folder), name)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(Hold):
            list_dir(str(alias / "outside"))

    def test_bounded_scan_reports_truncation(self):
        for i in range(12):
            (self.folder / str(i)).write_text("x")
        with patch.object(files, "MAX_SCAN", 3):
            result = list_dir(str(self.folder))
        self.assertTrue(result.limited)
        self.assertEqual(len(result.entries), 3)

    def test_replaced_root_and_entry_are_not_previewed(self):
        name = "notes.txt"
        path = self.folder / name
        path.write_text("first")
        listing = list_dir(str(self.folder))
        expected = dict(expected_root=listing.identity,
                        expected_entry=listing.entries[0].identity)
        path.unlink()
        path.write_text("second")
        with self.assertRaisesRegex(Hold, "file_changed"):
            preview(str(self.folder), name, **expected)
        self.folder.rename(self.root / "old")
        self.folder.mkdir()
        (self.folder / name).write_text("replacement")
        with self.assertRaisesRegex(Hold, "folder_changed"):
            preview(str(self.folder), name, **expected)

    def test_swap_to_symlink_between_metadata_check_and_open(self):
        path = self.folder / "notes.txt"
        path.write_text("safe")
        real_open = os.open
        def swap(name, flags, *args, **kwargs):
            if name == "notes.txt":
                path.unlink()
                path.symlink_to(self.outside / "secret.txt")
            return real_open(name, flags, *args, **kwargs)
        with patch.object(files.os, "open", side_effect=swap):
            with self.assertRaises(Hold):
                preview(str(self.folder), path.name)

    def test_revocation_before_publication_and_nul_beyond_header(self):
        path = self.folder / "notes.txt"
        path.write_bytes(b"a" * 5000 + b"\x00")
        with self.assertRaisesRegex(Hold, "file_not_text"):
            preview(str(self.folder), path.name)
        path.write_text("safe")
        real_read = os.read
        finished_reading = []
        def read(*args):
            data = real_read(*args)
            if not data:
                finished_reading.append(True)
            return data
        def revoke():
            if finished_reading:
                raise Hold("revoked")
        with patch.object(files.os, "read", side_effect=read):
            with self.assertRaisesRegex(Hold, "revoked"):
                preview(str(self.folder), path.name, checkpoint=revoke)
        self.assertTrue(finished_reading)


class TextSearchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "notes.txt"

    def test_search_uses_full_bounded_file_and_preserves_line_numbers(self):
        self.path.write_text("\n\n" + "x" * 9000 + "\nA.B literal\naxb not literal\n", encoding="utf-8")
        result = files.search_text(str(self.root), self.path.name, "a.b")
        self.assertEqual(result.matching_lines, 1)
        self.assertEqual(result.matches, (files.Match(4, "A.B literal"),))

    def test_search_counts_all_matching_lines_but_caps_snippets(self):
        self.path.write_text(("x" * 500 + "NEEDLE" + "y" * 500 + "\n") * 45)
        result = files.search_text(str(self.root), self.path.name, "needle")
        self.assertEqual(result.matching_lines, 45)
        self.assertEqual(len(result.matches), files.MAX_MATCHES)
        self.assertTrue(all("NEEDLE" in m.snippet and len(m.snippet) <= files.MAX_SNIPPET + 2
                            for m in result.matches))

    def test_bad_query_and_changed_file_are_refused(self):
        self.path.write_text("old")
        for query in ("", "x" * 129, "a\nb", None):
            with self.assertRaisesRegex(Hold, "invalid_file_query"):
                files.search_text(str(self.root), self.path.name, query)
        listing = list_dir(str(self.root))
        self.path.unlink()
        self.path.write_text("new")
        with self.assertRaisesRegex(Hold, "file_changed"):
            files.search_text(str(self.root), self.path.name, "new",
                expected_root=listing.identity, expected_entry=listing.entries[0].identity)
