// SPDX-License-Identifier: AGPL-3.0-only
//! Disposable native v2 denial-history adapter. No active grants, effects,
//! process evidence, canonical digest generation, or runtime owner authority.
pub mod guarded;

use super::{configure, schema_rows, validate_records, version, Error, Id, Store};
use rusqlite::{params, Connection, TransactionBehavior};

const MIGRATION: &str = include_str!("../../store/002_harness_replay.sql");
pub type Digest = [u8; 32];

pub struct ReplayStore {
    db: Connection,
}

fn migration_body() -> Result<&'static str, Error> {
    MIGRATION
        .split_once("BEGIN IMMEDIATE;\n")
        .and_then(|(_, body)| body.strip_suffix("COMMIT;\n"))
        .ok_or(Error::Schema)
}

fn check_integrity(db: &Connection) -> Result<(), Error> {
    let check: String = db.query_row("PRAGMA quick_check(1)", [], |r| r.get(0))?;
    if check != "ok" || db.prepare("PRAGMA foreign_key_check")?.exists([])? {
        return Err(Error::Schema);
    }
    validate_records(db)
}

fn check_replay(db: &Connection) -> Result<(), Error> {
    let invalid: bool = db.query_row(
        "SELECT EXISTS(
           SELECT 1 FROM harness_wave_replay w JOIN sessions s ON s.id=w.session_id
           WHERE s.project_id IS NOT w.project_id OR w.peer_count <>
              (SELECT count(*) FROM harness_connection_replay c
               WHERE c.session_id=w.session_id AND c.attempt=w.attempt)
           UNION ALL
           SELECT 1 FROM harness_wave_replay GROUP BY session_id HAVING count(*)>1024
           UNION ALL
           SELECT 1 FROM harness_connection_replay GROUP BY session_id HAVING count(*)>1024
           UNION ALL
           SELECT 1 FROM harness_approval_replay GROUP BY session_id HAVING count(*)>1024
        )",
        [],
        |r| r.get(0),
    )?;
    if invalid {
        return Err(Error::Schema);
    }
    Ok(())
}

fn valid_digest(id: &Digest) -> bool {
    *id != [0; 32]
}

impl ReplayStore {
    /// Explicit upgrade of a trusted, disposable, idle v1 fixture.
    /// Schema validation, idle check and migration share one writer transaction.
    pub fn upgrade_fixture(mut db: Connection) -> Result<Self, Error> {
        configure(&db)?;
        let reference = Store::initialize(Connection::open_in_memory()?)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if version(&tx)? != 1 || schema_rows(&tx)? != schema_rows(&reference.db)? {
            return Err(Error::Schema);
        }
        check_integrity(&tx)?;
        let busy: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM turns WHERE state IN ('pending','cancelled'))",
            [],
            |r| r.get(0),
        )?;
        if busy {
            return Err(Error::Busy);
        }
        tx.execute_batch(migration_body()?)?;
        if version(&tx)? != 2 {
            return Err(Error::Schema);
        }
        tx.commit()?;
        Ok(Self { db })
    }

    /// Only trusted fixture files; never interpret a restart as authorization.
    pub fn reopen(mut db: Connection) -> Result<Self, Error> {
        configure(&db)?;
        let reference = Store::initialize(Connection::open_in_memory()?)?;
        let expected = Self::upgrade_fixture(reference.db)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Deferred)?;
        if version(&tx)? != 2 || schema_rows(&tx)? != schema_rows(&expected.db)? {
            return Err(Error::Schema);
        }
        check_integrity(&tx)?;
        check_replay(&tx)?;
        tx.commit()?;
        Ok(Self { db })
    }

    /// Atomically burn the entire planned identity set. No execution ticket.
    pub fn claim_wave(
        &mut self,
        project: Id,
        session: Id,
        label: &str,
        attempt: Digest,
        peers: &[(Digest, Digest)],
    ) -> Result<(), Error> {
        if label.is_empty()
            || label.len() > 128
            || !label
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
            || !valid_digest(&attempt)
            || peers.is_empty()
            || peers.len() > 10
            || peers
                .iter()
                .any(|(worker, connection)| !valid_digest(worker) || !valid_digest(connection))
        {
            return Err(Error::Invalid);
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute(
            "INSERT INTO harness_wave_replay VALUES (?1,?2,?3,?4,?5)",
            params![
                session.as_slice(),
                project.as_slice(),
                label,
                attempt.as_slice(),
                peers.len() as i64
            ],
        )?;
        for (worker, connection) in peers {
            tx.execute(
                "INSERT INTO harness_connection_replay VALUES (?1,?2,?3,?4)",
                params![
                    session.as_slice(),
                    attempt.as_slice(),
                    worker.as_slice(),
                    connection.as_slice()
                ],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    /// Burn denial history. No receipt is created or restored.
    pub fn burn_approval(
        &mut self,
        session: Id,
        attempt: Digest,
        worker: Digest,
        request: Digest,
        action: Digest,
    ) -> Result<(), Error> {
        if [&attempt, &worker, &request, &action]
            .into_iter()
            .any(|id| !valid_digest(id))
        {
            return Err(Error::Invalid);
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute(
            "INSERT INTO harness_approval_replay VALUES (?1,?2,?3,?4,?5)",
            params![
                session.as_slice(),
                attempt.as_slice(),
                worker.as_slice(),
                request.as_slice(),
                action.as_slice()
            ],
        )?;
        tx.commit()?;
        Ok(())
    }
}
