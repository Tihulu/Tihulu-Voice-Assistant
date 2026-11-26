# SPDX-License-Identifier: AGPL-3.0-only
"""Real child processes exercise descriptor inheritance without real audio devices."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold


@unittest.skipUnless(os.path.isdir("/proc/self/fd"), "Linux procfs required")
class AudioDescriptorHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.model = self.root / "model.onnx"
        self.model.write_bytes(b"original")
        self.config = self.root / "model.onnx.json"
        self.config.write_bytes(b"config-original")
        self.output = self.root / "result"
        self.fixture = self.root / "engine"
        self.fixture.write_text("#!" + sys.executable + "\n" +
            "import sys\nfrom pathlib import Path\n"
            "a=sys.argv[1:]\n"
            "m=Path(a[a.index('--model' if '--model' in a else '-m')+1]).read_bytes()\n"
            "c=Path(a[a.index('--config')+1]).read_bytes() if '--config' in a else b''\n"
            "Path(a[-1]).write_bytes(m+c)\n")
        self.fixture.chmod(0o700)

    def replace(self, path):
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(b"substituted")
        replacement.replace(path)

    async def exercise(self, name, flag, expected):
        real_spawn = asyncio.create_subprocess_exec
        inherited = []
        async def spawn(*args, **kwargs):
            inherited.extend(kwargs["pass_fds"])
            self.replace(self.model)
            if name == "piper":
                self.replace(self.config)
            return await real_spawn(*args, **kwargs)
        with patch.object(audio.shutil, "which", return_value=str(self.fixture)), \
             patch.object(audio.asyncio, "create_subprocess_exec", side_effect=spawn):
            async with audio.child(name, flag, self.model, self.output) as p:
                self.assertEqual(await p.wait(), 0)
        self.assertEqual(self.output.read_bytes(), expected)
        self.assertEqual(len(inherited), 2 if name == "piper" else 1)
        for fd in inherited:
            with self.assertRaises(OSError):
                os.fstat(fd)

    async def test_whisper_child_reads_original_inode_after_path_replacement(self):
        await self.exercise("whisper-cli", "-m", b"original")

    async def test_piper_child_reads_original_weights_and_sidecar(self):
        await self.exercise("piper", "--model", b"originalconfig-original")

    async def test_piper_config_is_immutable_after_same_inode_write(self):
        real_spawn = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            config_alias = args[args.index("--config") + 1]
            self.config.write_bytes(b"mutated in place")
            with self.assertRaises(OSError):
                with open(config_alias, "r+b") as stream:
                    stream.write(b"tamper")
            return await real_spawn(*args, **kwargs)
        with patch.object(audio.shutil, "which", return_value=str(self.fixture)), \\
             patch.object(audio.asyncio, "create_subprocess_exec", side_effect=spawn):
            async with audio.child("piper", "--model", self.model, self.output) as p:
                self.assertEqual(await p.wait(), 0)
        self.assertEqual(self.output.read_bytes(), b"originalconfig-original")

    async def test_sidecar_sealing_failure_refuses_spawn(self):
        with patch.object(audio.shutil, "which", return_value=str(self.fixture)), \\
             patch.object(audio.os, "memfd_create", side_effect=OSError("fixture")), \\
             patch.object(audio.asyncio, "create_subprocess_exec") as spawn:
            with self.assertRaisesRegex(Hold, "audio_model_descriptor_unavailable"):
                async with audio.child("piper", "--model", self.model, self.output):
                    self.fail("unexpected child")
            spawn.assert_not_called()

    async def test_spawn_error_retains_backend_failure_reason(self):
        with patch.object(audio.shutil, "which", return_value=str(self.fixture)), \
             patch.object(audio.asyncio, "create_subprocess_exec", side_effect=OSError("fixture")):
            for name, args in (("arecord", ()), ("whisper-cli", ("-m", self.model))):
                with self.subTest(name=name), self.assertRaisesRegex(Hold, "audio_start_failed"):
                    async with audio.child(name, *args):
                        self.fail("unexpected child")

    async def test_symlink_is_refused_before_child_spawn(self):
        link = self.root / "link.onnx"
        link.symlink_to(self.model)
        with patch.object(audio.shutil, "which", return_value=str(self.fixture)), \
             patch.object(audio.asyncio, "create_subprocess_exec") as spawn:
            with self.assertRaisesRegex(Hold, "model_file_changed"):
                async with audio.child("piper", "--model", link, self.output):
                    self.fail("unexpected child")
            spawn.assert_not_called()
