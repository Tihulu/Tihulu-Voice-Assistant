# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic reference transactions for a future Rust SQLite adapter.

All IDs are trusted-owner inputs, not authentication or process-exit evidence.
Never attach this fixture to an existing application database.
"""
from contextlib import contextmanager


class Refused(ValueError):
    pass


@contextmanager
def transaction(db):
    if db.in_transaction:
        raise Refused("nested transaction")
    if db.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise Refused("foreign keys disabled")
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def identity(value):
    if type(value) is not bytes or len(value) != 16:
        raise Refused("invalid identity")


def text_value(value):
    if type(value) is not str or not value.strip() or "\0" in value:
        raise Refused("invalid text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise Refused("invalid text") from exc
    if size > 16384:
        raise Refused("invalid text")


def request_key(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or any(not (ch.isascii() and (ch.isalnum() or ch == "-")) for ch in value)):
        raise Refused("invalid request key")


def revision_value(value):
    if type(value) is not int or not 0 <= value < 2**63 - 1:
        raise Refused("invalid revision")


def session_row(db, session):
    row = db.execute(
        "SELECT revision, archived FROM sessions WHERE id=?", (session,)
    ).fetchone()
    if row is None:
        raise Refused("unknown session")
    revision_value(row[0])
    return row


def advance(db, session, revision):
    if db.execute(
        "UPDATE sessions SET revision=revision+1 WHERE id=? AND revision=?",
        (session, revision),
    ).rowcount != 1:
        raise Refused("stale revision")


def receipt(db, session, client, key):
    return db.execute(
        "SELECT t.user_seq, m.body, t.state FROM turns t JOIN messages m"
        " ON m.session_id=t.session_id AND m.seq=t.user_seq"
        " WHERE t.session_id=? AND t.client_id=? AND t.request_key=?",
        (session, client, key),
    ).fetchone()


def submit(db, session, client, expected, key, text):
    identity(session)
    identity(client)
    revision_value(expected)
    request_key(key)
    text_value(text)
    with transaction(db):
        revision, archived = session_row(db, session)
        old = receipt(db, session, client, key)
        if old is not None:
            if old[1] != text:
                raise Refused("request conflict")
            return ("replay", old[0], old[2])
        if archived or expected != revision:
            raise Refused("archived or stale")
        if db.execute(
            "SELECT 1 FROM turns WHERE session_id=? AND state IN ('pending','cancelled')",
            (session,),
        ).fetchone():
            raise Refused("busy")
        seq = db.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE session_id=?",
            (session,),
        ).fetchone()[0]
        if seq >= 1024:
            raise Refused("message limit")
        db.execute("INSERT INTO messages VALUES (?,?,'user',?)", (session, seq, text))
        db.execute(
            "INSERT INTO turns (session_id,client_id,request_key,user_seq,state)"
            " VALUES (?,?,?,?,'pending')", (session, client, key, seq),
        )
        advance(db, session, revision)
        return ("new", seq, "pending")


def transition(db, session, client, key, target, *, expected=None, text=None):
    identity(session)
    identity(client)
    request_key(key)
    if target == "completed":
        text_value(text)
    elif target == "cancelled":
        revision_value(expected)
    elif target != "drained":
        raise Refused("invalid target")
    with transaction(db):
        revision, archived = session_row(db, session)
        old = receipt(db, session, client, key)
        required = "cancelled" if target == "drained" else "pending"
        if archived or old is None or old[2] != required:
            raise Refused("terminal or unknown turn")
        if target == "cancelled" and expected != revision:
            raise Refused("stale revision")
        reply = None
        if target == "completed":
            reply = old[0] + 1
            db.execute(
                "INSERT INTO messages VALUES (?,?,'assistant',?)", (session, reply, text)
            )
        db.execute(
            "UPDATE turns SET state=?, assistant_seq=?"
            " WHERE session_id=? AND client_id=? AND request_key=?",
            (target, reply, session, client, key),
        )
        advance(db, session, revision)
        return revision + 1
