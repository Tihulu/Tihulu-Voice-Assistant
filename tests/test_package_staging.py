# SPDX-License-Identifier: AGPL-3.0-only
"""Regression tests for the Debian builder, without pip, network or install."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class PackageStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / "packaging/deb/build.sh"
        self.script.parent.mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / "packaging/deb/build.sh"
        shutil.copyfile(source, self.script)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.work = self.root / "staging"

    def run_builder(self):
        # No control file exists in this synthetic repo: even the fresh-path
        # case stops before pip, dpkg or any real packaging operations.
        return subprocess.run(["sh", str(self.script), str(self.work),
                               str(self.wheels)], capture_output=True,
                              text=True, timeout=10)

    def test_existing_directory_and_contents_preserved(self):
        self.work.mkdir()
        sentinel = self.work / "sentinel.txt"
        sentinel.write_bytes(b"synthetic data")
        result = self.run_builder()
        self.assertEqual(result.returncode, 2)
        self.assertIn("staging directory must not exist", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"synthetic data")
        self.assertEqual(list(self.work.iterdir()), [sentinel])

    def test_existing_empty_directory_refused(self):
        self.work.mkdir()
        result = self.run_builder()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_existing_symlink_and_dangling_symlink_preserved(self):
        target = self.root / "target"
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_bytes(b"synthetic data")
        self.work.symlink_to(target, target_is_directory=True)
        result = self.run_builder()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(self.work.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b"synthetic data")
        self.work.unlink()
        missing = self.root / "missing"
        self.work.symlink_to(missing, target_is_directory=True)
        result = self.run_builder()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(self.work.is_symlink())
        self.assertFalse(missing.exists())

    def test_fresh_path_reaches_build_and_has_normal_root_permissions(self):
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("control", result.stderr)
        self.assertTrue((self.work / "DEBIAN").is_dir())
        self.assertEqual(self.work.stat().st_mode & 0o777, 0o755)

    def test_missing_wheels_does_not_create_staging(self):
        self.wheels.rmdir()
        result = self.run_builder()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.work.exists())
