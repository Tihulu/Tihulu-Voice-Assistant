# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable SQLite denial-history reference; never grants or starts work."""
from pathlib import Path

from transactions import Refused, identity, transaction

MIGRATION = Path(__file__).with_name("002_harness_replay.sql").read_text()


def migrate_fixture(db):
    """Explicit v1 fixture upgrade, not a production migration or import parser."""
    if (db.in_transaction or db.execute("PRAGMA foreign_keys").fetchone() != (1,)
            or db.execute("PRAGMA user_version").fetchone() != (1,)):
        raise Refused("not idle v1 fixture")
    try:
        db.executescript(MIGRATION)
    except BaseException:
        db.rollback()
        raise


def digest(value):
    if type(value) is not bytes or len(value) != 32 or value == bytes(32):
        raise Refused("invalid digest")


def checked_version(db):
    if db.execute("PRAGMA user_version").fetchone() != (2,):
        raise Refused("not v2 replay fixture")


def claim_wave(db, project, session, label, attempt, peers):
    """Burn all identities atomically before any future effect. Never unburn."""
    identity(project)
    identity(session)
    digest(attempt)
    if (type(label) is not str or not 1 <= len(label) <= 128
            or any(not (c.isascii() and (c.isalnum() or c in "_.-")) for c in label)):
        raise Refused("invalid wave label")
    if type(peers) not in (tuple, list) or not 1 <= len(peers) <= 10:
        raise Refused("invalid peer count")
    for pair in peers:
        if type(pair) not in (tuple, list) or len(pair) != 2:
            raise Refused("invalid peer")
        digest(pair[0])
        digest(pair[1])
    with transaction(db):
        checked_version(db)
        db.execute("INSERT INTO harness_wave_replay VALUES (?,?,?,?)",
                   (session, project, label, attempt))
        db.executemany("INSERT INTO harness_connection_replay VALUES (?,?,?,?)",
                       [(session, attempt, worker, connection) for worker, connection in peers])


def burn_approval(db, session, attempt, worker, request, action):
    """Persist denial history only; no active receipt can be reconstructed."""
    identity(session)
    for value in (attempt, worker, request, action):
        digest(value)
    with transaction(db):
        checked_version(db)
        db.execute("INSERT INTO harness_approval_replay VALUES (?,?,?,?,?)",
                   (session, attempt, worker, request, action))
