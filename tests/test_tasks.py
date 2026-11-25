# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Board tests: no device, engine or endpoint is used."""
import unittest

from tihulu_jarvis.tasks import MAX_TASKS, STATES, Board
from tihulu_jarvis.voice.m1 import Hold


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()

    def test_a_task_states_what_it_is_doing_and_when(self):
        task_id = self.board.start("turn", "naber nasilsin", now=10.0)
        row = self.board.get(task_id)
        self.assertEqual((row.kind, row.state, row.started_at, row.ended_at),
                         ("turn", "running", 10.0, 0.0))
        self.assertTrue(row.busy)
        self.board.finish(task_id, "done", now=15.0)
        row = self.board.get(task_id)
        self.assertFalse(row.busy)
        self.assertEqual((row.state, row.reason, row.ended_at), ("done", "", 15.0))
        self.assertEqual(row.elapsed(18.0), 5.0)
        self.assertEqual(row.elapsed(180.0), 5.0)

    def test_progress_is_bytes_or_nothing_never_a_guess(self):
        task_id = self.board.start("download", "fixture-weights", now=1.0)
        self.board.update(task_id, counted=400, counted_total=1000)
        row = self.board.get(task_id)
        self.assertEqual((row.counted, row.counted_total), (400, 1000))
        # A turn has no bytes to count, and none are invented.
        turn = self.board.start("turn", "naber nasilsin")
        self.assertEqual((self.board.get(turn).counted, self.board.get(turn).counted_total),
                         (0, 0))

    def test_every_state_is_reachable_and_validated(self):
        task_id = self.board.start("catalog_fetch", "fetch a catalog")
        for state in STATES:
            if state in ("queued", "running"):
                continue
            self.board.finish(task_id, state)
            self.assertEqual(self.board.get(task_id).state, state)
        with self.assertRaises(Hold):
            self.board.finish(task_id, "almost-done")

    def test_cancelling_states_the_reason(self):
        task_id = self.board.start("transcription", "clip.wav", now=2.0)
        self.board.cancel(task_id, "turn_cancelled", now=3.0)
        row = self.board.get(task_id)
        self.assertEqual((row.state, row.reason), ("cancelled", "turn_cancelled"))
        self.assertEqual(self.board.busy(), ())

    def test_the_board_is_bounded_and_ids_do_not_reuse(self):
        for index in range(MAX_TASKS + 10):
            self.board.start("turn", "naber nasilsin %d" % index, now=float(index))
        self.assertEqual(len(self.board.rows), MAX_TASKS)
        self.assertEqual(self.board.rows[0].label, "naber nasilsin 10")
        with self.assertRaisesRegex(Hold, "task_not_found"):
            self.board.get(1)

    def test_a_bad_task_is_refused_before_it_is_recorded(self):
        for bad in ("", "   ", "x" * 200, None, 5):
            with self.subTest(label=bad):
                with self.assertRaises(Hold):
                    self.board.start("turn", bad)
        with self.assertRaises(Hold):
            self.board.start("vision", "naber nasilsin")

    def test_locks_held_by_running_tasks_are_listed(self):
        first = self.board.start("turn", "naber nasilsin", ("microphone",))
        second = self.board.start("transcription", "clip.wav", ("audio_playback",))
        self.assertEqual(self.board.locks(), ((first, "microphone"), (second, "audio_playback")))
        self.board.finish(first)
        self.assertEqual(self.board.locks(), ((second, "audio_playback"),))
