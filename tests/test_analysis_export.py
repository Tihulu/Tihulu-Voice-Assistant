# SPDX-License-Identifier: AGPL-3.0-only
import csv
import hashlib
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tihulu_jarvis.analysis_controller import Artifact
from tihulu_jarvis.analysis_export import encode, write_new
from tihulu_jarvis.temperature_engine import analyze_temperature, render_svg
from tihulu_jarvis.voice.m1 import Hold


class ExportTests(unittest.TestCase):
    def setUp(self):
        series = analyze_temperature(b"time_s,temperature_c\n0,20\n10,22\n20,24\n30,26\n40,28\n")
        svg = render_svg(series)
        self.artifact = Artifact("id", "task", None, "temperature-series/1", series,
                                 "blue", svg, hashlib.sha256(svg).hexdigest(), "en", "summary")

    def test_svg_exact_and_digest_checked(self):
        self.assertEqual(encode(self.artifact, "svg"), self.artifact.svg)
        with self.assertRaisesRegex(Hold, "export_digest_mismatch"):
            encode(replace(self.artifact, svg=b"modified"), "svg")

    def test_numeric_data_csv(self):
        rows = list(csv.reader(encode(self.artifact, "data").decode().splitlines()))
        self.assertEqual(rows[0], ["time_s", "temperature_c"])
        self.assertEqual([tuple(map(float, row)) for row in rows[1:]],
                         list(self.artifact.series.points))

    def test_summary_csv_values_and_units(self):
        rows = list(csv.reader(encode(self.artifact, "summary").decode().splitlines()))
        self.assertEqual(rows[0], ["metric", "value", "unit"])
        self.assertEqual({row[0]: (float(row[1]), row[2]) for row in rows[1:]},
                         {"count": (5, "samples"), "mean": (24, "degC"),
                          "minimum": (20, "degC"), "maximum": (28, "degC"),
                          "change": (8, "degC"), "duration": (40, "s")})

    def test_png_requires_trusted_encoder(self):
        with self.assertRaisesRegex(Hold, "export_png_unavailable"):
            encode(self.artifact, "png")
        with self.assertRaisesRegex(Hold, "export_png_invalid"):
            encode(self.artifact, "png", png=lambda svg: b"not png")

    def test_unknown_format_rejected(self):
        with self.assertRaisesRegex(Hold, "export_invalid_format"):
            encode(self.artifact, ["svg"])


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX writer")
class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "result.csv"
        self.data = b"time_s,temperature_c\n0,20\n"

    def write(self, path=None, authorized=lambda: True):
        return write_new(str(path or self.target), "data", self.data, authorized=authorized)

    def test_private_exact_and_no_temporary_file(self):
        self.write()
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_never_overwrite_existing_file(self):
        self.target.write_bytes(b"original")
        with self.assertRaisesRegex(Hold, "export_destination_exists"):
            self.write()
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_symlink_target_not_followed(self):
        source = self.root / "source.csv"
        source.write_bytes(b"original")
        self.target.symlink_to(source)
        with self.assertRaisesRegex(Hold, "export_destination_exists"):
            self.write()
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(source.read_bytes(), b"original")

    def test_symlink_parent_and_missing_parent_rejected(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        for dest in (link / "result.csv", self.root / "missing" / "result.csv"):
            with self.assertRaisesRegex(Hold, "export_write_failed"):
                self.write(dest)
        self.assertFalse(list(real.iterdir()))
        self.assertFalse((self.root / "missing").exists())

    def test_shared_parent_rejected(self):
        folder = self.root / "shared"
        folder.mkdir()
        folder.chmod(0o777)
        with self.assertRaisesRegex(Hold, "export_destination_not_private"):
            self.write(folder / "result.csv")
        self.assertFalse(list(folder.iterdir()))

    def test_denied_before_io(self):
        with patch("os.open", side_effect=AssertionError("unexpected open")):
            with self.assertRaisesRegex(Hold, "export_not_authorized"):
                self.write(authorized=lambda: False)

    def test_revoked_before_publication(self):
        original = os.fsync
        allowed = True
        def revoke(fd):
            nonlocal allowed
            original(fd)
            allowed = False
        with patch("os.fsync", side_effect=revoke):
            with self.assertRaisesRegex(Hold, "export_not_authorized"):
                self.write(authorized=lambda: allowed)
        self.assertFalse(list(self.root.iterdir()))

    def test_invalid_destination_and_extension(self):
        for path in ("relative.csv", str(self.root) + "/../result.csv",
                     str(self.root) + "//result.csv"):
            with self.assertRaisesRegex(Hold, "export_invalid_destination"):
                self.write(path)
        with self.assertRaisesRegex(Hold, "export_wrong_extension"):
            self.write(self.root / "result.txt")
        self.assertFalse(list(self.root.iterdir()))

    def test_failed_write_cleans_temp(self):
        with patch("os.write", side_effect=OSError("private canary")):
            with self.assertRaisesRegex(Hold, "^export_write_failed$"):
                self.write()
        self.assertFalse(list(self.root.iterdir()))
