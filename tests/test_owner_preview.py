# SPDX-License-Identifier: AGPL-3.0-only
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import owner_preview as preview


class OwnerPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / "repo"
        self.root = self.base / "root"
        self.repo.mkdir()
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.source = preview.SOURCE + "__init__.py"
        self.target = preview.RUNTIME + "__init__.py"
        self.put(self.repo, self.source, b"# synthetic module\n")
        self.put(self.root, self.target, b"# synthetic module\n")
        for source, target in preview.EXTRA.items():
            self.put(self.repo, source, b"synthetic metadata\n")
            self.put(self.root, target, b"synthetic metadata\n")
        self.put(self.root, "usr/bin/tihulu-jarvis", preview.WRAPPER)
        (self.root / "usr/bin/tihulu-jarvis").chmod(0o755)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True)

    def put(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def verify(self):
        return preview.verify(self.repo, self.root, self.commit)

    def test_exact_tree_and_module_bytes(self):
        self.put(self.repo, self.source, b"uncommitted source mutation")
        evidence = self.verify()
        self.assertEqual(evidence["commit"], self.commit)
        self.assertEqual(evidence["modules"], 1)
        self.assertEqual(evidence["physical_acceptance"], "UNKNOWN")

    def test_wrong_commit_and_mutated_module(self):
        with self.assertRaisesRegex(preview.Refusal, "checkout_commit_mismatch"):
            preview.verify(self.repo, self.root, "0" * 40)
        self.put(self.root, self.target, b"changed")
        with self.assertRaisesRegex(preview.Refusal, "module_content_mismatch"):
            self.verify()

    def test_extra_module_and_wrapper_mutation(self):
        extra = self.root / preview.RUNTIME / "unexpected.py"
        extra.write_text("unexpected")
        with self.assertRaisesRegex(preview.Refusal, "unexpected_runtime_files"):
            self.verify()
        extra.unlink()
        self.put(self.root, "usr/bin/tihulu-jarvis", b"changed")
        with self.assertRaisesRegex(preview.Refusal, "wrapper_mismatch"):
            self.verify()

    def test_symlinked_module_refused(self):
        target = self.root / self.target
        target.unlink()
        target.symlink_to(self.repo / self.source)
        with self.assertRaisesRegex(preview.Refusal, "unexpected_symlink"):
            self.verify()

    def test_prepare_is_disposable_and_does_not_start_anything(self):
        evidence = self.verify()
        state = self.base / "state"
        state.mkdir(mode=0o700)
        output = io.StringIO()
        with patch.object(preview.tempfile, "mkdtemp", return_value=str(state)), \
                patch.object(preview.subprocess, "run", side_effect=AssertionError("no launch")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(preview.prepare(self.root, evidence), state)
        report = json.loads((state / "acceptance.json").read_text())
        self.assertEqual(set(report["scenarios"].values()), {"UNKNOWN"})
        self.assertEqual(report["clean_install"], "NOT_RUN")
        self.assertTrue((state / "temperature.csv").is_file())
        self.assertIn("Close the stable Jarvis normally", output.getvalue())
