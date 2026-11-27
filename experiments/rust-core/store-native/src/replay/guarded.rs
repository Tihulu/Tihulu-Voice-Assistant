// SPDX-License-Identifier: AGPL-3.0-only
//! Native disposable v3 fixture. The persisted recovery barrier is not
//! a process handle, lease, authentication token or permission grant.
use super::{check_integrity, check_replay, valid_digest, Digest, ReplayStore};
use crate::{configure, schema_rows, version, Error, Id, Store};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::collections::BTreeSet;

const MIGRATION: &str = include_str!("../../../store/003_harness_recovery.sql");
pub type Peer = (Digest, Digest);

#[derive(Debug, Eq, PartialEq)]
pub struct Unresolved {
    pub attempt: Digest,
    pub peers: Vec<Peer>,
}

pub struct GuardedReplayStore {
    store: ReplayStore,
}

fn check_guard(db: &Connection) -> Result<(), Error> {
    let invalid: bool = db.query_row(
        "SELECT EXISTS(
            SELECT 1 FROM harness_wave_replay w WHERE NOT EXISTS(
                SELECT 1 FROM harness_recovery r
                WHERE r.session_id=w.session_id AND r.attempt=w.attempt
            )
            UNION ALL
            SELECT 1 FROM harness_recovery r JOIN sessions s ON s.id=r.session_id
            WHERE s.archived=1 AND r.state='unresolved'
        )",
        [],
        |r| r.get(0),
    )?;
    if invalid {
        return Err(Error::Schema);
    }
    Ok(())
}

fn peers_for(db: &Connection, session: Id, attempt: Digest) -> Result<Vec<Peer>, Error> {
    let mut stmt = db.prepare(
        "SELECT worker,connection FROM harness_connection_replay
         WHERE session_id=?1 AND attempt=?2 ORDER BY worker LIMIT 11",
    )?;
    let rows = stmt.query_map(params![session.as_slice(), attempt.as_slice()], |r| {
        Ok((r.get(0)?, r.get(1)?))
    })?;
    let peers = rows.collect::<rusqlite::Result<Vec<Peer>>>()?;
    if peers.is_empty() || peers.len() > 10 {
        return Err(Error::Schema);
    }
    Ok(peers)
}

impl GuardedReplayStore {
    /// v2 history cannot prove whether old work drained. Refuse migration
    /// with ANY prior harness admission rather than assuming it is safe.
    pub fn upgrade_fixture(mut db: Connection) -> Result<Self, Error> {
        configure(&db)?;
        let reference = Store::initialize(Connection::open_in_memory()?)?;
        let expected = ReplayStore::upgrade_fixture(reference.db)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if version(&tx)? != 2 || schema_rows(&tx)? != schema_rows(&expected.db)? {
            return Err(Error::Schema);
        }
        check_integrity(&tx)?;
        check_replay(&tx)?;
        let busy: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM harness_wave_replay
                UNION ALL SELECT 1 FROM turns WHERE state IN ('pending','cancelled'))",
            [],
            |r| r.get(0),
        )?;
        if busy {
            return Err(Error::Busy);
        }
        let body = MIGRATION
            .split_once("BEGIN IMMEDIATE;\n")
            .and_then(|(_, body)| body.strip_suffix("COMMIT;\n"))
            .ok_or(Error::Schema)?;
        tx.execute_batch(body)?;
        if version(&tx)? != 3 {
            return Err(Error::Schema);
        }
        tx.commit()?;
        Ok(Self {
            store: ReplayStore { db },
        })
    }

    pub fn reopen(mut db: Connection) -> Result<Self, Error> {
        configure(&db)?;
        let reference = Store::initialize(Connection::open_in_memory()?)?;
        let v2 = ReplayStore::upgrade_fixture(reference.db)?;
        let expected = Self::upgrade_fixture(v2.db)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Deferred)?;
        if version(&tx)? != 3 || schema_rows(&tx)? != schema_rows(&expected.store.db)? {
            return Err(Error::Schema);
        }
        check_integrity(&tx)?;
        check_replay(&tx)?;
        check_guard(&tx)?;
        tx.commit()?;
        Ok(Self {
            store: ReplayStore { db },
        })
    }

    pub fn claim_wave(
        &mut self,
        project: Id,
        session: Id,
        label: &str,
        attempt: Digest,
        peers: &[Peer],
    ) -> Result<(), Error> {
        // The SQL trigger inserts the unresolved barrier in the very same
        // writer transaction as the wave and all of its peer identities.
        self.store.claim_wave(project, session, label, attempt, peers)
    }

    pub fn burn_approval(
        &mut self,
        session: Id,
        attempt: Digest,
        worker: Digest,
        request: Digest,
        action: Digest,
    ) -> Result<(), Error> {
        self.store
            .burn_approval(session, attempt, worker, request, action)
    }

    pub fn unresolved(&self, session: Id) -> Result<Option<Unresolved>, Error> {
        let tx = self.store.db.unchecked_transaction()?;
        let exists: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM sessions WHERE id=?1)",
            [session.as_slice()],
            |r| r.get(0),
        )?;
        if !exists {
            return Err(Error::Unknown);
        }
        let attempt: Option<Digest> = tx
            .query_row(
                "SELECT attempt FROM harness_recovery WHERE session_id=?1 AND state='unresolved'",
                [session.as_slice()],
                |r| r.get(0),
            )
            .optional()?;
        let result = attempt
            .map(|attempt| {
                Ok::<_, Error>(Unresolved {
                    attempt,
                    peers: peers_for(&tx, session, attempt)?,
                })
            })
            .transpose()?;
        tx.commit()?;
        Ok(result)
    }

    /// TRUSTED owner observation of all peers never starting or being reaped.
    /// Exact matching is necessary, NOT sufficient proof of OS process exit.
    /// No caller may use database rows alone as evidence of drain.
    pub fn observe_drained(
        &mut self,
        session: Id,
        attempt: Digest,
        observed: &[Peer],
    ) -> Result<(), Error> {
        if !valid_digest(&attempt) || observed.is_empty() || observed.len() > 10 {
            return Err(Error::Invalid);
        }
        let supplied: BTreeSet<_> = observed.iter().copied().collect();
        if supplied.len() != observed.len() {
            return Err(Error::Invalid);
        }
        let tx = self
            .store
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let expected: BTreeSet<_> = peers_for(&tx, session, attempt)?.into_iter().collect();
        if expected != supplied {
            return Err(Error::Conflict);
        }
        let count = tx.execute(
            "UPDATE harness_recovery SET state='drained'
             WHERE session_id=?1 AND attempt=?2 AND state='unresolved'",
            params![session.as_slice(), attempt.as_slice()],
        )?;
        if count != 1 {
            return Err(Error::Conflict);
        }
        tx.commit()?;
        Ok(())
    }
}
