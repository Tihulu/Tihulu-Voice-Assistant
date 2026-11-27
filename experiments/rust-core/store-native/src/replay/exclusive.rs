// SPDX-License-Identifier: AGPL-3.0-only
//! Linux cooperating private-directory owner for disposable v3 fixtures.
//! Same lock and path checks as v1. Not a hostile-filesystem sandbox.
use super::guarded::{GuardedReplayStore, Peer, Unresolved};
use super::{Digest, ReplayStore};
use crate::admission::{AdmissionError, GuardedWave};
use crate::exclusive::open_locked;
use crate::{Error, Id, Store};
use jarvis_core::session::Session;
use std::fs::File;
use std::path::Path;

pub struct ExclusiveReplay {
    store: GuardedReplayStore,
    lock: File,
}

impl ExclusiveReplay {
    /// Create a fresh private fixture; never replace an existing database.
    pub fn initialize(directory: &Path, project: Id, session: Id) -> Result<Self, Error> {
        if project == [0; 16] || session == [0; 16] {
            return Err(Error::Invalid);
        }
        let (db, _, lock) = open_locked(directory, true)?;
        let v1 = Store::initialize(db)?;
        v1.project(project, "Fixture")?;
        v1.session(session, Some(project))?;
        let v2 = ReplayStore::upgrade_fixture(v1.db)?;
        let store = GuardedReplayStore::upgrade_fixture(v2.db)?;
        Ok(Self { store, lock })
    }

    pub fn reopen(directory: &Path) -> Result<Self, Error> {
        let (db, _, lock) = open_locked(directory, false)?;
        let store = GuardedReplayStore::reopen(db)?;
        Ok(Self { store, lock })
    }

    pub fn unresolved(&self, session: Id) -> Result<Option<Unresolved>, Error> {
        self.store.unresolved(session)
    }

    /// Trusted local recovery only. Matching IDs alone are not OS evidence.
    pub fn observe_drained(
        &mut self,
        session: Id,
        attempt: Digest,
        peers: &[Peer],
    ) -> Result<(), Error> {
        self.store.observe_drained(session, attempt, peers)
    }

    /// Consumes the exclusive owner. The same lock lives inside the wave;
    /// no second cooperating owner can reopen until the wave is dropped.
    pub fn into_wave(
        self,
        runtime: Session,
        project: Id,
        session: Id,
    ) -> Result<GuardedWave, AdmissionError> {
        let Self { store, lock } = self;
        GuardedWave::new(store, runtime, project, session).map(|wave| wave.retain_lock(lock))
    }
}
