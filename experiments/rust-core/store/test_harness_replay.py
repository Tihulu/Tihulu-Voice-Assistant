# SPDX-License-Identifier: AGPL-3.0-only
import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from harness_replay import burn_approval, claim_wave, migrate_fixture
from test_schema import SCHEMA, P, S, T, connect
from transactions import Refused

A, W, C, R, X = (bytes([n]) * 32 for n in range(1, 6))


class HarnessReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fixture.sqlite"
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        with self.db:
            self.db.execute("INSERT INTO projects VALUES (?,?)", (P, "Fixture"))
            self.db.execute("INSERT INTO sessions (id,project_id) VALUES (?,?)", (S, P))
            self.db.execute("INSERT INTO sessions (id) VALUES (?)", (T,))
        migrate_fixture(self.db)

    def count(self, table):
        return self.db.execute("SELECT count(*) FROM harness_" + table + "_replay").fetchone()[0]

    def claim(self, db=None):
        claim_wave(db or self.db, P, S, "one", A, [(W, C)])

    def test_reopen_retains_label_attempt_connection_and_approval(self):
        self.claim()
        burn_approval(self.db, S, A, W, R, X)
        second = connect(self.path)
        self.addCleanup(second.close)
        for label, attempt, connection in (
            ("one", X, R), ("two", A, R), ("two", X, C)
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                claim_wave(second, P, S, label, attempt, [(W, connection)])
        for request, action in ((R, A), (A, X)):
            with self.assertRaises(sqlite3.IntegrityError):
                burn_approval(second, S, A, W, request, action)
        self.assertEqual([self.count(t) for t in ("wave", "connection", "approval")], [1, 1, 1])

    def test_peer_failure_rolls_back_entire_wave(self):
        with self.assertRaises(sqlite3.IntegrityError):
            claim_wave(self.db, P, S, "one", A, [(W, C), (X, C)])
        self.assertEqual(self.count("wave"), 0)
        self.assertEqual(self.count("connection"), 0)
        self.claim()

    def test_scope_and_orphan_approval_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            claim_wave(self.db, P, T, "one", A, [(W, C)])
        self.claim()
        with self.assertRaises(sqlite3.IntegrityError):
            burn_approval(self.db, S, A, X, R, X)
        self.assertEqual(self.count("approval"), 0)
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("UPDATE sessions SET project_id=NULL WHERE id=?", (S,))

    def test_no_replace_delete_or_update_even_without_recursive_triggers(self):
        self.claim()
        burn_approval(self.db, S, A, W, R, X)
        self.db.execute("PRAGMA recursive_triggers=OFF")
        for table, values in (
            ("wave", (S, P, "one", A, 1)),
            ("connection", (S, A, W, C)),
            ("approval", (S, A, W, R, X)),
        ):
            name = "harness_" + table + "_replay"
            for query, params in (
                ("DELETE FROM " + name, ()),
                ("UPDATE " + name + " SET session_id=session_id", ()),
                ("INSERT OR REPLACE INTO " + name + " VALUES ("
                 + ",".join("?" for _ in values) + ")", values),
            ):
                with self.assertRaises(sqlite3.IntegrityError), self.db:
                    self.db.execute(query, params)
            self.assertEqual(self.count(table), 1)

    def test_two_connections_only_one_claim_commits(self):
        barrier = threading.Barrier(2, timeout=5)

        def worker():
            db = connect(self.path)
            try:
                barrier.wait()
                try:
                    self.claim(db)
                    return "claimed"
                except sqlite3.IntegrityError:
                    return "denied"
            finally:
                db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=10) for f in futures), ["claimed", "denied"])
        self.assertEqual(self.count("connection"), 1)

    def test_archive_disallows_new_history_without_erasing_old(self):
        self.claim()
        with self.db:
            self.db.execute("UPDATE sessions SET archived=1 WHERE id=?", (S,))
        with self.assertRaises(sqlite3.IntegrityError):
            burn_approval(self.db, S, A, W, R, X)
        with self.assertRaises(sqlite3.IntegrityError):
            claim_wave(self.db, P, S, "two", X, [(W, R)])
        self.assertEqual(self.count("wave"), 1)

    def test_validation_and_fk_disabled_fail_closed(self):
        with self.assertRaises(Refused):
            claim_wave(self.db, P, S, "one", A, [(W, C)] * 11)
        with self.assertRaises(Refused):
            claim_wave(self.db, P, S, "one", bytes(32), [(W, C)])
        self.db.execute("PRAGMA foreign_keys=OFF")
        with self.assertRaises(Refused):
            self.claim()
        self.assertEqual(self.count("wave"), 0)

    def test_migration_cannot_be_reapplied(self):
        with self.assertRaises(Refused):
            migrate_fixture(self.db)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone(), (2,))

    def test_approval_limit_keeps_original_tombstone(self):
        self.claim()
        for i in range(1, 1025):
            value = i.to_bytes(32, "big")
            burn_approval(self.db, S, A, W, value, value)
        with self.assertRaises(sqlite3.IntegrityError):
            burn_approval(self.db, S, A, W, R, X)
        self.assertEqual(self.count("approval"), 1024)
        with self.assertRaises(sqlite3.IntegrityError):
            burn_approval(self.db, S, A, W, (1).to_bytes(32, "big"), X)


    def test_completed_wave_cannot_gain_additional_peer(self):
        self.claim()
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("INSERT INTO harness_connection_replay VALUES (?,?,?,?)",
                            (S, A, X, R))
        self.assertEqual(self.count("connection"), 1)
        burn_approval(self.db, S, A, W, R, X)

    def test_incomplete_direct_plan_never_admits_approval_history(self):
        # Direct fixture SQL can commit an incomplete plan, but cannot
        # admit any approval for it. The supported claim API is atomic.
        with self.db:
            self.db.execute("INSERT INTO harness_wave_replay VALUES (?,?,?,?,?)",
                            (S, P, "one", A, 2))
            self.db.execute("INSERT INTO harness_connection_replay VALUES (?,?,?,?)",
                            (S, A, W, C))
        second = connect(self.path)
        self.addCleanup(second.close)
        with self.assertRaises(sqlite3.IntegrityError):
            burn_approval(second, S, A, W, R, X)
        self.assertEqual(self.count("approval"), 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.claim(second)

    def test_parent_replace_cannot_reparent_history(self):
        self.claim()
        self.db.execute("PRAGMA recursive_triggers=OFF")
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("INSERT OR REPLACE INTO sessions (id,project_id) VALUES (?,NULL)",
                            (S,))
        self.assertEqual(self.db.execute("SELECT project_id FROM sessions WHERE id=?", (S,))
                         .fetchone(), (P,))
        self.assertEqual(self.count("wave"), 1)

    def test_two_peer_plan_remains_closed_across_reopen(self):
        claim_wave(self.db, P, S, "one", A, [(W, C), (X, R)])
        second = connect(self.path)
        self.addCleanup(second.close)
        fresh = bytes([9]) * 32
        with self.assertRaises(sqlite3.IntegrityError), second:
            second.execute("INSERT INTO harness_connection_replay VALUES (?,?,?,?)",
                           (S, A, fresh, fresh))
        burn_approval(second, S, A, X, R, W)
        self.assertEqual(self.count("connection"), 2)
        self.assertEqual(self.count("approval"), 1)


if __name__ == "__main__":
    unittest.main()
