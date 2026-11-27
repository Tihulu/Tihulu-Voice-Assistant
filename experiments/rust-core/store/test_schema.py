# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable SQLite contract tests; not the Rust production store."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCHEMA = Path(__file__).with_name("001.sql").read_text(encoding="utf-8")
P = b"p" * 16
S = b"s" * 16
T = b"t" * 16
C = b"c" * 16
D = b"d" * 16


def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    return db


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "fixture.sqlite"
        self.db = connect(self.path)
        self.addCleanup(lambda: self.db.close())
        self.db.executescript(SCHEMA)
        with self.db:
            self.db.execute("INSERT INTO projects VALUES (?, ?)", (P, "Fixture"))
            self.db.execute("INSERT INTO sessions (id, project_id) VALUES (?, ?)", (S, P))
            self.db.execute("INSERT INTO sessions (id) VALUES (?)", (T,))

    def message(self, session, seq, role="user", body="Hello"):
        self.db.execute("INSERT INTO messages VALUES (?, ?, ?, ?)", (session, seq, role, body))

    def turn(self, session, seq, key="one", client=C):
        self.db.execute(
            "INSERT INTO turns (session_id, client_id, request_key, user_seq, state)"
            " VALUES (?, ?, ?, ?, 'pending')",
            (session, client, key, seq),
        )

    def submit(self, session=S, seq=1, key="one", client=C, body="Hello"):
        with self.db:
            self.message(session, seq, body=body)
            self.turn(session, seq, key, client)

    def state(self, session=S):
        return self.db.execute(
            "SELECT state FROM turns WHERE session_id=? ORDER BY user_seq", (session,)
        ).fetchall()

    def test_version_and_foreign_keys(self):
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone(), (1,))
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone(), (1,))
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("INSERT INTO sessions (id, project_id) VALUES (?, ?)", (D, C))

    def test_reopen_keeps_receipt_and_pending_barrier(self):
        self.submit()
        self.db.close()
        self.db = connect(self.path)
        row = self.db.execute(
            "SELECT t.user_seq, m.body, t.state FROM turns t JOIN messages m"
            " ON m.session_id=t.session_id AND m.seq=t.user_seq"
            " WHERE t.session_id=? AND t.client_id=? AND t.request_key=?",
            (S, C, "one"),
        ).fetchone()
        self.assertEqual(row, (1, "Hello", "pending"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit(seq=2, key="two")
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone(), (1,))

    def test_cancelled_turn_requires_drain(self):
        self.submit()
        with self.db:
            self.db.execute("UPDATE turns SET state='cancelled' WHERE session_id=?", (S,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit(seq=2, key="two", client=D)
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("UPDATE sessions SET archived=1 WHERE id=?", (S,))
        with self.db:
            self.db.execute("UPDATE turns SET state='drained' WHERE session_id=?", (S,))
        self.submit(seq=2, key="two", client=D)
        self.assertEqual(self.state(), [("drained",), ("pending",)])

    def test_cross_session_and_role_mismatch(self):
        with self.db:
            self.message(T, 1)
            self.message(S, 2, "assistant")
        for seq in (1, 2):
            with self.assertRaises(sqlite3.IntegrityError), self.db:
                self.turn(S, seq)
        self.assertEqual(self.state(), [])

    def test_completion_is_atomic_and_bound_to_adjacent_reply(self):
        self.submit()
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.message(S, 3, "assistant")
            self.db.execute(
                "UPDATE turns SET state='completed', assistant_seq=3 WHERE session_id=?", (S,)
            )
        self.assertEqual(self.state(), [("pending",)])
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone(), (1,))
        with self.db:
            self.message(S, 2, "assistant", "Done")
            self.db.execute(
                "UPDATE turns SET state='completed', assistant_seq=2 WHERE session_id=?", (S,)
            )
        self.assertEqual(self.state(), [("completed",)])

    def test_terminal_receipt_cannot_restart_or_change_identity(self):
        self.submit()
        with self.db:
            self.db.execute("UPDATE turns SET state='cancelled' WHERE session_id=?", (S,))
            self.db.execute("UPDATE turns SET state='drained' WHERE session_id=?", (S,))
        for sql in (
            "UPDATE turns SET state='pending'",
            "UPDATE turns SET request_key='changed'",
            "UPDATE turns SET state='completed', assistant_seq=2",
        ):
            with self.assertRaises(sqlite3.IntegrityError), self.db:
                self.db.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit(seq=2)
        self.submit(seq=2, client=D)
        self.assertEqual(self.state(), [("drained",), ("pending",)])

    def test_archive_rejects_new_turns(self):
        with self.db:
            self.db.execute("UPDATE sessions SET archived=1 WHERE id=?", (S,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit()
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone(), (0,))
        self.submit(session=T)
        self.assertEqual(self.state(T), [("pending",)])

    def test_invalid_ids_body_keys_and_sequences(self):
        for seq, body in ((0, "x"), (1025, "x"), (1, ""), (1, "x\0y"), (1, "x"*16385)):
            with self.assertRaises(sqlite3.IntegrityError), self.db:
                self.message(S, seq, body=body)
        for key in ("", "a/b", "a\0b", "x"*129):
            with self.assertRaises(sqlite3.IntegrityError):
                self.submit(key=key)
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit(client=b"short")
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages").fetchone(), (0,))

    def test_schema_reapply_fails_without_partial_changes(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.db.executescript(SCHEMA)
        self.db.rollback()
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone(), (1,))
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone(), (2,))


if __name__ == "__main__":
    unittest.main()
