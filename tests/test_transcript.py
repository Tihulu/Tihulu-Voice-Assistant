# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Export tests: only temporary files in a private directory are written."""
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.transcript import MAX_BYTES, MAX_MESSAGES, render, write
from tihulu_jarvis.voice.m1 import Hold, Message


def history(*items):
    return tuple(Message("user" if index % 2 == 0 else "assistant", item)
                 for index, item in enumerate(items))


class RenderTests(unittest.TestCase):
    def test_messages_are_rendered_with_their_speaker(self):
        text = render(history("naber nasilsin", "Merhaba!"))
        self.assertEqual(text, "user: naber nasilsin\nassistant: Merhaba!\n")

    def test_a_header_states_the_fields_and_nothing_else(self):
        head = render(history("naber"), started=1.0)
        self.assertIn("# TIHULU JARVIS conversation exported locally", head)
        self.assertIn("fields: speaker, time, text. Nothing else is recorded.", head)

    def test_unknown_entries_are_skipped(self):
        mixed = history("naber nasilsin") + ((None, "x"), 5)
        self.assertEqual(render(mixed), "user: naber nasilsin\n")

    def test_the_render_is_bounded_by_messages_and_bytes(self):
        # The bound is on messages, not on one speaker: half of them are users.
        rendered = render(history(*["naber"] * (MAX_MESSAGES + 20)))
        self.assertEqual(rendered.count("user:"), MAX_MESSAGES // 2)
        self.assertEqual(rendered.count("assistant:"), MAX_MESSAGES // 2)
        out = render(history(*["x" * 3000] * 400))
        self.assertIn("# truncated:", out)
        self.assertLessEqual(len(out.encode("utf-8")), MAX_BYTES + 200)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_a_write_is_atomic_and_private(self):
        path = str(self.tmp / "conversation.txt")
        self.assertEqual(write(path, "user: naber\n"), path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "user: naber\n")
        self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)

    def test_a_relative_path_or_oversized_text_is_refused(self):
        with self.assertRaises(Hold):
            write("relative.txt", "user: naber\n")
        with self.assertRaisesRegex(Hold, "export_too_large"):
            write(str(self.tmp / "big.txt"), "x" * (MAX_BYTES + 1))

    def test_a_failed_write_leaves_no_half_file(self):
        path = str(self.tmp / "conversation.txt")
        with self.assertRaises(Hold):
            write(path, "x" * (MAX_BYTES + 1))
        self.assertFalse(Path(path).exists())
        leftovers = [p.name for p in self.tmp.iterdir() if p.name.startswith("tihulu-export-")]
        self.assertEqual(leftovers, [])
