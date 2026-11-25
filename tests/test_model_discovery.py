# SPDX-License-Identifier: AGPL-3.0-only
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tihulu_jarvis import model_discovery as discovery
from tihulu_jarvis.voice.m1 import Hold


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_only_regular_nonempty_audio_candidates_in_selected_tree(self):
        folder = self.root / "models"
        folder.mkdir()
        (folder / "voice.onnx").write_bytes(b"x")
        (folder / "whisper.bin").write_bytes(b"x")
        (folder / "empty.bin").touch()
        (folder / "other.gguf").write_bytes(b"x")
        (folder / "outside.onnx").symlink_to(folder / "voice.onnx")
        os.mkfifo(folder / "pipe.bin")
        (folder / "child").mkdir()
        (folder / "child" / "voice2.onnx").write_bytes(b"x")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.bin").write_bytes(b"x")
        (folder / "escape").symlink_to(outside, target_is_directory=True)
        result = discovery.scan(str(folder))
        self.assertEqual([(x.relative, x.kind) for x in result.candidates],
                         [("child/voice2.onnx", "tts"), ("voice.onnx", "tts"),
                          ("whisper.bin", "stt")])
        self.assertFalse(result.limited)

    def test_rejects_symlink_root_and_relative_path(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for root in (str(alias), str(alias) + "/", "relative"):
            with self.assertRaises(Hold):
                discovery.scan(root)

    def test_caps_entries_and_candidates(self):
        for i in range(10):
            (self.root / ("%d.bin" % i)).write_bytes(b"x")
        with patch.object(discovery, "MAX_ENTRIES", 3):
            result = discovery.scan(str(self.root))
            self.assertTrue(result.limited)
            self.assertLessEqual(len(result.candidates), 3)
        with patch.object(discovery, "MAX_CANDIDATES", 2):
            result = discovery.scan(str(self.root))
            self.assertTrue(result.limited)
            self.assertEqual(len(result.candidates), 2)

    def test_depth_limit_and_invalid_native_names(self):
        folder = self.root
        for name in ("a", "b", "c"):
            folder = folder / name
            folder.mkdir()
        (folder / "deep.bin").write_bytes(b"x")
        (self.root / "bad\nname.bin").write_bytes(b"x")
        result = discovery.scan(str(self.root))
        self.assertTrue(result.limited)
        self.assertFalse(result.candidates)

    def test_checkpoint_revocation_never_returns_partial_inventory(self):
        (self.root / "model.bin").write_bytes(b"x")
        calls = []
        def checkpoint():
            calls.append(1)
            if len(calls) == 3:
                raise Hold("revoked")
        with self.assertRaisesRegex(Hold, "revoked"):
            discovery.scan(str(self.root), checkpoint)
