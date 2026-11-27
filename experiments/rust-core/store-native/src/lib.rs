// SPDX-License-Identifier: AGPL-3.0-only
#![forbid(unsafe_code)]
//! Disposable native SQLite fixture. Only a trusted owner supplies connections
//! and IDs. No path-opening API, authentication, process control or migration.

use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use std::time::Duration;

const SCHEMA: &str = include_str!("../../store/001.sql");
pub type Id = [u8; 16];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Database,
    Invalid,
    Schema,
    Unknown,
    Conflict,
    Busy,
    Archived,
    Limit,
}

impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self {
        // Avoid leaking SQL text, paths or private record contents.
        Self::Database
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum State {
    Pending,
    Cancelled,
    Drained,
    Completed,
}

impl State {
    fn parse(value: &str) -> Result<Self, Error> {
        match value {
            "pending" => Ok(Self::Pending),
            "cancelled" => Ok(Self::Cancelled),
            "drained" => Ok(Self::Drained),
            "completed" => Ok(Self::Completed),
            _ => Err(Error::Schema),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Receipt {
    pub sequence: i64,
    pub state: State,
    pub replay: bool,
}

#[derive(Debug, Eq, PartialEq)]
pub struct Message {
    pub sequence: i64,
    pub role: String,
    pub body: String,
}

struct Previous {
    seq: i64,
    body: String,
    state: State,
}

type SchemaRow = (String, String, String, String);

pub struct Store {
    db: Connection,
}

fn configure(db: &Connection) -> Result<(), Error> {
    if !db.is_autocommit() || rusqlite::version_number() < 3_054_001 {
        return Err(Error::Schema);
    }
    db.busy_timeout(Duration::from_secs(2))?;
    db.execute_batch("PRAGMA foreign_keys=ON; PRAGMA trusted_schema=OFF;")?;
    let fk: i64 = db.query_row("PRAGMA foreign_keys", [], |r| r.get(0))?;
    if fk != 1 {
        return Err(Error::Schema);
    }
    Ok(())
}

fn version(db: &Connection) -> Result<i64, Error> {
    Ok(db.query_row("PRAGMA user_version", [], |r| r.get(0))?)
}

fn schema_rows(db: &Connection) -> Result<Vec<SchemaRow>, Error> {
    let mut stmt = db.prepare(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema
         WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name LIMIT 100",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
    })?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

fn text_valid(text: &str) -> bool {
    !text.trim().is_empty() && text.len() <= 16_384 && !text.contains('\0')
}

fn key_valid(key: &str) -> bool {
    !key.is_empty()
        && key.len() <= 128
        && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
}

fn session_row(db: &Connection, session: Id) -> Result<(i64, bool), Error> {
    let row: Option<(i64, bool)> = db
        .query_row(
            "SELECT revision,archived FROM sessions WHERE id=?1",
            [session.as_slice()],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    let (rev, archived) = row.ok_or(Error::Unknown)?;
    if !(0..i64::MAX).contains(&rev) {
        return Err(Error::Limit);
    }
    Ok((rev, archived))
}

fn previous(
    db: &Connection,
    session: Id,
    client: Id,
    key: &str,
) -> Result<Option<Previous>, Error> {
    let row: Option<(i64, String, String)> = db
        .query_row(
            "SELECT t.user_seq,m.body,t.state FROM turns t JOIN messages m
             ON m.session_id=t.session_id AND m.seq=t.user_seq
             WHERE t.session_id=?1 AND t.client_id=?2 AND t.request_key=?3",
            params![session.as_slice(), client.as_slice(), key],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .optional()?;
    row.map(|(seq, body, state)| {
        Ok(Previous {
            seq,
            body,
            state: State::parse(&state)?,
        })
    })
    .transpose()
}

fn advance(tx: &Transaction<'_>, session: Id, rev: i64) -> Result<i64, Error> {
    let count = tx.execute(
        "UPDATE sessions SET revision=revision+1 WHERE id=?1 AND revision=?2",
        params![session.as_slice(), rev],
    )?;
    if count != 1 {
        return Err(Error::Conflict);
    }
    Ok(rev + 1)
}

enum Transition<'a> {
    Cancel(i64),
    Complete(&'a str),
    Drain,
}

impl Store {
    /// Initialize only an empty connection owned by the test harness.
    pub fn initialize(db: Connection) -> Result<Self, Error> {
        configure(&db)?;
        if version(&db)? != 0 || !schema_rows(&db)?.is_empty() {
            return Err(Error::Schema);
        }
        db.execute_batch(SCHEMA)?;
        Ok(Self { db })
    }

    /// Reopen an already trusted fixture. Never treats a restart as drain.
    /// Schema equality and consistency checks are not a sandbox for imports.
    pub fn reopen(db: Connection) -> Result<Self, Error> {
        configure(&db)?;
        let reference = Self::initialize(Connection::open_in_memory()?)?;
        if version(&db)? != 1 || schema_rows(&db)? != schema_rows(&reference.db)? {
            return Err(Error::Schema);
        }
        let check: String = db.query_row("PRAGMA quick_check(1)", [], |r| r.get(0))?;
        if check != "ok" {
            return Err(Error::Schema);
        }
        let mut stmt = db.prepare("PRAGMA foreign_key_check")?;
        if stmt.exists([])? {
            return Err(Error::Schema);
        }
        drop(stmt);
        Ok(Self { db })
    }

    pub fn project(&self, id: Id, name: &str) -> Result<(), Error> {
        if !text_valid(name) || name.len() > 128 {
            return Err(Error::Invalid);
        }
        self.db.execute(
            "INSERT INTO projects VALUES (?1,?2)",
            params![id.as_slice(), name],
        )?;
        Ok(())
    }

    pub fn session(&self, id: Id, project: Option<Id>) -> Result<(), Error> {
        self.db.execute(
            "INSERT INTO sessions(id,project_id) VALUES (?1,?2)",
            params![id.as_slice(), project.as_ref().map(|p| p.as_slice())],
        )?;
        Ok(())
    }

    pub fn history(&self, session: Id) -> Result<(i64, Vec<Message>), Error> {
        let (rev, _) = session_row(&self.db, session)?;
        let mut stmt = self.db.prepare(
            "SELECT seq,role,body FROM messages WHERE session_id=?1 ORDER BY seq LIMIT 1025",
        )?;
        let rows = stmt.query_map([session.as_slice()], |r| {
            Ok(Message {
                sequence: r.get(0)?,
                role: r.get(1)?,
                body: r.get(2)?,
            })
        })?;
        let messages = rows.collect::<rusqlite::Result<Vec<_>>>()?;
        if messages.len() > 1024 {
            return Err(Error::Limit);
        }
        Ok((rev, messages))
    }

    pub fn submit(
        &mut self,
        session: Id,
        client: Id,
        expected: i64,
        key: &str,
        text: &str,
    ) -> Result<Receipt, Error> {
        if !key_valid(key) || !text_valid(text) || !(0..i64::MAX).contains(&expected) {
            return Err(Error::Invalid);
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (rev, archived) = session_row(&tx, session)?;
        if let Some(old) = previous(&tx, session, client, key)? {
            if old.body != text {
                return Err(Error::Conflict);
            }
            tx.commit()?;
            return Ok(Receipt {
                sequence: old.seq,
                state: old.state,
                replay: true,
            });
        }
        if archived {
            return Err(Error::Archived);
        }
        if rev != expected {
            return Err(Error::Conflict);
        }
        let live: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM turns WHERE session_id=?1
             AND state IN ('pending','cancelled'))",
            [session.as_slice()],
            |r| r.get(0),
        )?;
        if live {
            return Err(Error::Busy);
        }
        let seq: i64 = tx.query_row(
            "SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE session_id=?1",
            [session.as_slice()],
            |r| r.get(0),
        )?;
        if !(1..1024).contains(&seq) {
            return Err(Error::Limit);
        }
        tx.execute(
            "INSERT INTO messages VALUES (?1,?2,'user',?3)",
            params![session.as_slice(), seq, text],
        )?;
        tx.execute(
            "INSERT INTO turns(session_id,client_id,request_key,user_seq,state)
             VALUES (?1,?2,?3,?4,'pending')",
            params![session.as_slice(), client.as_slice(), key, seq],
        )?;
        advance(&tx, session, rev)?;
        tx.commit()?;
        Ok(Receipt {
            sequence: seq,
            state: State::Pending,
            replay: false,
        })
    }

    fn transition(
        &mut self,
        session: Id,
        client: Id,
        key: &str,
        action: Transition<'_>,
    ) -> Result<i64, Error> {
        if !key_valid(key) {
            return Err(Error::Invalid);
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (rev, archived) = session_row(&tx, session)?;
        if archived {
            return Err(Error::Archived);
        }
        let old = previous(&tx, session, client, key)?.ok_or(Error::Unknown)?;
        let (target, reply) = match action {
            Transition::Cancel(expected) if old.state == State::Pending && expected == rev => {
                ("cancelled", None)
            }
            Transition::Drain if old.state == State::Cancelled => ("drained", None),
            Transition::Complete(text) if old.state == State::Pending && text_valid(text) => {
                let seq = old.seq + 1;
                tx.execute(
                    "INSERT INTO messages VALUES (?1,?2,'assistant',?3)",
                    params![session.as_slice(), seq, text],
                )?;
                ("completed", Some(seq))
            }
            _ => return Err(Error::Conflict),
        };
        let count = tx.execute(
            "UPDATE turns SET state=?1,assistant_seq=?2
             WHERE session_id=?3 AND client_id=?4 AND request_key=?5",
            params![target, reply, session.as_slice(), client.as_slice(), key],
        )?;
        if count != 1 {
            return Err(Error::Conflict);
        }
        let next = advance(&tx, session, rev)?;
        tx.commit()?;
        Ok(next)
    }

    pub fn cancel(
        &mut self,
        session: Id,
        client: Id,
        key: &str,
        expected: i64,
    ) -> Result<i64, Error> {
        self.transition(session, client, key, Transition::Cancel(expected))
    }

    /// Trusted terminal observation; caller must establish exact owned exit.
    pub fn drain(&mut self, session: Id, client: Id, key: &str) -> Result<i64, Error> {
        self.transition(session, client, key, Transition::Drain)
    }

    /// Trusted runtime supplies the reply, not an arbitrary network client.
    pub fn complete(
        &mut self,
        session: Id,
        client: Id,
        key: &str,
        text: &str,
    ) -> Result<i64, Error> {
        self.transition(session, client, key, Transition::Complete(text))
    }
}
