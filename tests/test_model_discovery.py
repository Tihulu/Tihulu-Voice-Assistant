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

    def test_rejects_symlink_in_any_root_component(self):
        outside = self.root / "outside"
        models = outside / "models"
        models.mkdir(parents=True)
        (models / "private.bin").write_bytes(b"x")
        alias = self.root / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        for selected in (alias / "models", str(alias / "models") + "/"):
            with self.subTest(selected=selected):
                with self.assertRaises(Hold):
                    discovery.scan(str(selected))
        self.assertEqual(discovery.scan(str(models)).candidates[0].relative,
                         "private.bin")

    def test_rejects_parent_traversal_in_selected_root(self):
        child = self.root / "child"
        child.mkdir()
        (self.root / "private.bin").write_bytes(b"x")
        with self.assertRaisesRegex(Hold, "model_folder_invalid"):
            discovery.scan(str(child / ".."))

    def test_checkpoint_revocation_during_root_open_closes_descriptor(self):
        real_open = os.open
        opened = []
        def track(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def checkpoint():
            if opened:
                raise Hold("revoked")
        with patch.object(discovery.os, "open", side_effect=track):
            with self.assertRaisesRegex(Hold, "revoked"):
                discovery.scan(str(self.root), checkpoint)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

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
        root_opened = False
        original = discovery._open_root
        def open_root(*args):
            nonlocal root_opened
            fd = original(*args)
            root_opened = True
            return fd
        def checkpoint():
            if root_opened:
                calls.append(1)
                if len(calls) == 3:
                    raise Hold("revoked")
        # Fail after the single candidate has been collected, at the final
        # checkpoint; root traversal must not turn this into an early abort test.
        with patch.object(discovery, "_open_root", side_effect=open_root):
            with self.assertRaisesRegex(Hold, "revoked"):
                discovery.scan(str(self.root), checkpoint)
        self.assertEqual(len(calls), 3)

    def test_selected_directory_identity_refuses_regular_replacement_before_scan(self):
        from tihulu_jarvis import files
        root=self.root/"chosen"
        root.mkdir()
        expected=files.root_identity(str(root))
        root.rename(self.root/"original")
        root.mkdir()
        (root/"replacement.bin").write_bytes(b"x")
        with patch.object(discovery.os,"scandir") as scan:
            with self.assertRaisesRegex(Hold,"folder_changed"):
                discovery.scan(str(root),expected_root=expected)
            scan.assert_not_called()
