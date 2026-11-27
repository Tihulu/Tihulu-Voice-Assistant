# SPDX-License-Identifier: AGPL-3.0-only
"""Run the same deterministic transaction trace as the native Rust fixture."""
import sqlite3
import unittest
from pathlib import Path

from test_schema import SCHEMA, S, T, C, D
from transactions import Refused, submit, transition


class ParityTests(unittest.TestCase):
    def test_shared_trace(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        ids = {"S": S, "T": T, "C": C, "D": D}
        with db:
            for session in (S, T):
                db.execute("INSERT INTO sessions (id) VALUES (?)", (session,))
        lines = Path(__file__).with_name("parity.tsv").read_text(encoding="utf-8").splitlines()
        count = 0
        for line in lines:
            if line.startswith("#"):
                continue
            count += 1
            op, session, client, rev, key, text, result, final_rev, messages = line.split("\t")
            session, client = ids[session], ids[client]
            with self.subTest(row=count):
                try:
                    if op == "submit":
                        kind, seq, state = submit(db, session, client, int(rev), key, text)
                        actual = f"{kind}:{seq}:{state}"
                    else:
                        target = {"complete": "completed", "cancel": "cancelled", "drain": "drained"}[op]
                        value = transition(db, session, client, key, target, expected=int(rev), text=text)
                        actual = f"rev:{value}"
                except Refused:
                    actual = "reject"
                self.assertEqual(actual, result)
                self.assertEqual(db.execute("SELECT revision FROM sessions WHERE id=?", (session,)).fetchone(), (int(final_rev),))
                self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE session_id=?", (session,)).fetchone(), (int(messages),))
        self.assertEqual(count, 25)
        print(f"Shared SQLite parity rows: {count}")


if __name__ == "__main__":
    unittest.main()
