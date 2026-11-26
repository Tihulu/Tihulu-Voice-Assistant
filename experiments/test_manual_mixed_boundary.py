# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from test_speech_result import wav
from mixed_voice_review import silence_boundaries
from manual_mixed_boundary import boundary_preview,verify_boundary_preview

class ManualMixedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.data=wav(b"\x00\x04"*24000+b"\x00\x05"*24000+b"\x00\x06"*24000)
        self.cuts=(0,24000,48000,72000)

    def test_no_pause_requires_explicit_boundaries_and_preserves_every_frame(self):
        with self.assertRaises(Refused):
            silence_boundaries(self.data)
        shown=boundary_preview(self.data,self.cuts)
        pieces=verify_boundary_preview(self.data,self.cuts,shown)
        self.assertEqual(b"".join(p.data[44:] for p in pieces),self.data[44:])
        self.assertEqual(tuple(p.digest for p in pieces),tuple(r.audio_digest for r in shown.rows))
        self.assertEqual(shown.cuts,self.cuts)

    def test_recut_or_changed_source_invalidates_preview(self):
        shown=boundary_preview(self.data,self.cuts)
        for data,cuts in ((self.data,(0,20000,48000,72000)),
                          (wav(b"\x00\x07"*72000),self.cuts)):
            with self.assertRaises(Refused):
                verify_boundary_preview(data,cuts,shown)

    def test_tampered_or_missing_snapshot_rejected(self):
        shown=boundary_preview(self.data,self.cuts)
        for bad in (None,shown.preview_digest,replace(shown,preview_digest="0"*64),
                    replace(shown,rows=shown.rows[::-1])):
            with self.assertRaises(Refused):
                verify_boundary_preview(self.data,self.cuts,bad)

    def test_malformed_or_too_short_boundaries_rejected(self):
        for bad in ((0,10000,72000),(0,24000,72001),(0,24000,24000,72000),
                    (False,24000,72000),[0,24000,72000]):
            with self.assertRaises(Refused):
                boundary_preview(self.data,bad)
