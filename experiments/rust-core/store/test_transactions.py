# SPDX-License-Identifier: AGPL-3.0-only
import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from test_schema import SCHEMA, P, S, T, C, D, connect
from transactions import Refused, submit, transition


class TransactionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fixture.sqlite"
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        with self.db:
            self.db.execute("INSERT INTO projects VALUES (?, ?)", (P, "Fixture"))
            self.db.execute("INSERT INTO sessions (id, project_id) VALUES (?, ?)", (S, P))
            self.db.execute("INSERT INTO sessions (id) VALUES (?)", (T,))

    def revision(self):
        return self.db.execute("SELECT revision FROM sessions WHERE id=?", (S,)).fetchone()[0]

    def test_exact_replay_across_connection_and_completion(self):
        self.assertEqual(submit(self.db, S, C, 0, "one", "Hello"), ("new", 1, "pending"))
        self.assertEqual(transition(self.db, S, C, "one", "completed", text="Done"), 2)
        second = connect(self.path)
        self.addCleanup(second.close)
        self.assertEqual(submit(second, S, C, 0, "one", "Hello"), ("replay", 1, "completed"))
        with self.assertRaises(Refused):
            submit(second, S, C, 0, "one", "Changed")
        self.assertEqual(self.revision(), 2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone(), (2,))

    def test_cancelled_pending_survives_reopen(self):
        submit(self.db, S, C, 0, "one", "Hello")
        transition(self.db, S, C, "one", "cancelled", expected=1)
        second = connect(self.path)
        self.addCleanup(second.close)
        with self.assertRaises(Refused):
            submit(second, S, D, 2, "two", "New")
        with self.assertRaises(Refused):
            transition(second, S, C, "one", "completed", text="Late")
        with self.assertRaises(Refused):
            transition(second, S, D, "one", "drained")
        self.assertEqual(transition(second, S, C, "one", "drained"), 3)
        self.assertEqual(submit(second, S, D, 3, "two", "New"), ("new", 2, "pending"))

    def test_parallel_same_request_exactly_one_new(self):
        barrier = threading.Barrier(2, timeout=5)

        def worker():
            db = connect(self.path)
            try:
                barrier.wait()
                return submit(db, S, C, 0, "one", "Hello")
            finally:
                db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            rows = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(row[0] for row in rows), ["new", "replay"])
        self.assertEqual(self.revision(), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone(), (1,))

    def test_revision_and_identity_scope(self):
        submit(self.db, S, C, 0, "one", "Hello")
        with self.assertRaises(Refused):
            submit(self.db, S, D, 0, "one", "Hello")
        with self.assertRaises(Refused):
            transition(self.db, S, D, "one", "completed", text="wrong")
        with self.assertRaises(Refused):
            transition(self.db, T, C, "one", "completed", text="wrong")
        self.assertEqual(submit(self.db, T, C, 0, "one", "Different"), ("new", 1, "pending"))
        self.assertEqual(self.revision(), 1)

    def test_revision_failure_rolls_back_message_and_receipt(self):
        self.db.execute(
            "CREATE TRIGGER fixture_abort BEFORE UPDATE OF revision ON sessions"
            " BEGIN SELECT RAISE(ABORT, 'fixture'); END"
        )
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            submit(self.db, S, C, 0, "one", "Hello")
        self.assertEqual(self.revision(), 0)
        for table in ("messages", "turns"):
            self.assertEqual(self.db.execute(f"SELECT count(*) FROM {table}").fetchone(), (0,))
        self.db.execute("DROP TRIGGER fixture_abort")
        self.db.commit()
        self.assertEqual(submit(self.db, S, C, 0, "one", "Hello"), ("new", 1, "pending"))

    def test_validation_and_disabled_fk_fail_closed(self):
        for expected, key, text in ((True, "one", "ok"), (0, "bad/key", "ok"),
                                    (0, "one", "\t"), (0, "one", "x\0y")):
            with self.assertRaises(Refused):
                submit(self.db, S, C, expected, key, text)
        self.db.execute("PRAGMA foreign_keys=OFF")
        with self.assertRaises(Refused):
            submit(self.db, S, C, 0, "one", "Hello")
        self.assertEqual(self.revision(), 0)


if __name__ == "__main__":
    unittest.main()
