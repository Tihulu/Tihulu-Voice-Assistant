# SPDX-License-Identifier: AGPL-3.0-only
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tihulu_jarvis.selected_csv import read_selected_csv
from tihulu_jarvis.temperature_engine import MAX_BYTES, analyze_temperature
from tihulu_jarvis.voice.m1 import Hold

DATA = b"time_s,temperature_c\n0,20\n10,22\n"


@unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor reader")
class SelectedCSVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "test.csv"
        self.path.write_bytes(DATA)

    def read(self, path=None, **kwargs):
        return read_selected_csv(str(path or self.path),
                                 authorized=kwargs.pop("authorized", lambda: True),
                                 **kwargs)

    def test_snapshot_is_exact_and_source_is_unchanged(self):
        snapshot = self.read()
        self.assertEqual(snapshot.data, DATA)
        self.assertEqual(self.path.read_bytes(), DATA)
        self.path.write_bytes(b"replacement")
        self.assertEqual(analyze_temperature(snapshot.data).mean_c, 21)
        self.assertEqual(snapshot.data, DATA)

    def test_denied_before_open(self):
        with patch("os.open", side_effect=AssertionError("unexpected open")):
            with self.assertRaisesRegex(Hold, "csv_selection_not_authorized"):
                self.read(authorized=lambda: False)
            with self.assertRaisesRegex(Hold, "analysis_cancelled"):
                self.read(cancelled=lambda: True)

    def test_requires_explicit_true(self):
        with self.assertRaisesRegex(Hold, "csv_selection_not_authorized"):
            self.read(authorized=lambda: "yes")

    def test_no_relative_dot_or_empty_component(self):
        for path in ("relative.csv", str(self.root) + "/../test.csv",
                     str(self.root) + "/./test.csv",
                     str(self.root) + "//test.csv"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(Hold, "csv_invalid_selection"):
                    self.read(path)

    def test_symlink_file_and_directory_rejected(self):
        link = self.root / "link.csv"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(Hold, "csv_not_regular_file"):
            self.read(link)
        directory = self.root / "real"
        directory.mkdir()
        (directory / "test.csv").write_bytes(DATA)
        alias = self.root / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(Hold, "csv_selection_unreadable"):
            self.read(alias / "test.csv")

    def test_nonregular_files_rejected_without_read(self):
        fifo = self.root / "pipe.csv"
        os.mkfifo(fifo)
        with patch("os.read", side_effect=AssertionError("unexpected read")):
            for path in (fifo, self.root):
                with self.assertRaisesRegex(Hold, "csv_not_regular_file"):
                    self.read(path)

    def test_byte_limits(self):
        for content in (b"", b"x" * (MAX_BYTES + 1)):
            self.path.write_bytes(content)
            with self.assertRaisesRegex(Hold, "csv_invalid_size"):
                self.read()
        self.path.write_bytes(b"x" * MAX_BYTES)
        self.assertEqual(len(self.read().data), MAX_BYTES)

    def test_mutation_during_read_is_rejected(self):
        original_read = os.read
        def mutate(fd, n):
            chunk = original_read(fd, n)
            if chunk:
                self.path.write_bytes(b"x")
            return chunk
        with patch("os.read", side_effect=mutate):
            with self.assertRaisesRegex(Hold, "csv_changed_during_read"):
                self.read()

    def test_revocation_during_read(self):
        original_read = os.read
        allowed = True
        def revoke(fd, n):
            nonlocal allowed
            result = original_read(fd, n)
            allowed = False
            return result
        with patch("os.read", side_effect=revoke):
            with self.assertRaisesRegex(Hold, "csv_selection_not_authorized"):
                self.read(authorized=lambda: allowed)

    def test_cancellation_during_read(self):
        original_read = os.read
        stopped = False
        def stop(fd, n):
            nonlocal stopped
            result = original_read(fd, n)
            stopped = True
            return result
        with patch("os.read", side_effect=stop):
            with self.assertRaisesRegex(Hold, "analysis_cancelled"):
                self.read(cancelled=lambda: stopped)

    def test_short_read_and_missing_file(self):
        with patch("os.read", return_value=b""):
            with self.assertRaisesRegex(Hold, "csv_changed_during_read"):
                self.read()
        self.path.unlink()
        with self.assertRaisesRegex(Hold, "^csv_selection_unreadable$"):
            self.read()


if __name__ == "__main__":
    unittest.main()
