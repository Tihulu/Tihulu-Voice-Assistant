// SPDX-License-Identifier: AGPL-3.0-only
//! Linux private-directory fixture. Advisory lock for cooperating owners only.
//! The trusted directory and its ancestors must not be replaced while open.
use crate::owned::{Admission, OwnedStore, Ticket};
use crate::{Error, Id, Message, Recovery, Store};
use rusqlite::{Connection, OpenFlags};
use std::fs::{self, File, OpenOptions, TryLockError};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

pub struct ExclusiveStore {
    // Drop the SQLite owner before releasing the lock.
    owner: OwnedStore,
    directory: PathBuf,
    _lock: File,
}

fn private_file(path: &Path) -> Result<(), Error> {
    let meta = fs::symlink_metadata(path).map_err(|_| Error::Database)?;
    if !meta.is_file() || meta.nlink() != 1 || meta.mode() & 0o077 != 0 {
        return Err(Error::Invalid);
    }
    Ok(())
}

fn private_dir(path: &Path) -> Result<(), Error> {
    let meta = fs::symlink_metadata(path).map_err(|_| Error::Database)?;
    if !meta.is_dir() || meta.mode() & 0o077 != 0 {
        return Err(Error::Invalid);
    }
    Ok(())
}

impl ExclusiveStore {
    /// Only the trusted local owner supplies the private, existing directory.
    /// No arbitrary remote paths, no lock deletion, no stale-PID takeover.
    pub fn open(directory: &Path, initialize: bool) -> Result<Self, Error> {
        private_dir(directory)?;
        let directory = fs::canonicalize(directory).map_err(|_| Error::Database)?;
        let lock_path = directory.join("owner.lock");
        let lock = match OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&lock_path)
        {
            Ok(file) => file,
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
                private_file(&lock_path)?;
                OpenOptions::new()
                    .read(true)
                    .write(true)
                    .open(&lock_path)
                    .map_err(|_| Error::Database)?
            }
            Err(_) => return Err(Error::Database),
        };
        match lock.try_lock() {
            Ok(()) => {}
            Err(TryLockError::WouldBlock) => return Err(Error::Busy),
            Err(TryLockError::Error(_)) => return Err(Error::Database),
        }
        let db_path = directory.join("fixture.sqlite");
        for name in [
            "fixture.sqlite-wal",
            "fixture.sqlite-shm",
            "fixture.sqlite-journal",
        ] {
            let sidecar = directory.join(name);
            match fs::symlink_metadata(&sidecar) {
                Ok(_) => private_file(&sidecar)?,
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => return Err(Error::Database),
            }
        }
        if initialize {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&db_path)
                .map_err(|_| Error::Database)?;
        }
        private_file(&db_path)?;
        let db = Connection::open_with_flags(&db_path, OpenFlags::SQLITE_OPEN_READ_WRITE)?;
        let store = if initialize {
            Store::initialize(db)?
        } else {
            Store::reopen(db)?
        };
        Ok(Self {
            owner: OwnedStore::new(store),
            directory,
            _lock: lock,
        })
    }

    /// Create a validated, durable snapshot under the same private directory.
    /// Existing backups are never overwritten. No live job is copied.
    /// This is a local fixture backup, not a disaster-recovery system.
    pub fn backup_idle(&self) -> Result<(), Error> {
        let destination = self.directory.join("backup.sqlite");
        let staged = self.directory.join("backup.staging");
        if fs::symlink_metadata(&destination).is_ok() {
            return Err(Error::Conflict);
        }
        let staging = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&staged)
            .map_err(|_| Error::Database)?;
        drop(staging);
        let result: Result<(), Error> = (|| {
            self.owner.backup_idle(&staged)?;
            private_file(&staged)?;
            let db = Connection::open_with_flags(&staged, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
            let snapshot = Store::reopen(db)?;
            drop(snapshot);
            File::open(&staged)
                .and_then(|f| f.sync_all())
                .map_err(|_| Error::Database)?;
            // Hard-link publication fails if a destination already exists.
            // The private directory is trusted; no other writer may replace it.
            fs::hard_link(&staged, &destination).map_err(|_| Error::Database)?;
            Ok(())
        })();
        let cleanup = fs::remove_file(&staged).map_err(|_| Error::Database);
        let sync = File::open(&self.directory)
            .and_then(|f| f.sync_all())
            .map_err(|_| Error::Database);
        result?;
        cleanup?;
        sync?;
        Ok(())
    }

    /// Inspect a trusted snapshot without exposing a runnable Store or grants.
    pub fn backup_history(&self, session: Id) -> Result<(i64, Vec<Message>), Error> {
        let path = self.directory.join("backup.sqlite");
        private_file(&path)?;
        let db = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        Store::reopen(db)?.history(session)
    }

    pub fn project(&self, id: Id, name: &str) -> Result<(), Error> {
        self.owner.project(id, name)
    }

    pub fn session(&self, id: Id, project: Option<Id>) -> Result<(), Error> {
        self.owner.session(id, project)
    }

    pub fn history(&self, session: Id) -> Result<(i64, Vec<Message>), Error> {
        self.owner.history(session)
    }

    pub fn recovery(&self, session: Id) -> Result<Option<Recovery>, Error> {
        self.owner.recovery(session)
    }

    pub fn submit(
        &mut self,
        session: Id,
        client: Id,
        expected: i64,
        key: &str,
        text: &str,
    ) -> Result<Admission, Error> {
        self.owner.submit(session, client, expected, key, text)
    }

    pub fn recover_cancel(&mut self, session: Id, expected: i64) -> Result<Ticket, Error> {
        self.owner.recover_cancel(session, expected)
    }

    pub fn cancel(&mut self, ticket: &Ticket, expected: i64) -> Result<i64, Error> {
        self.owner.cancel(ticket, expected)
    }

    pub fn complete_reaped(&mut self, ticket: &Ticket, text: &str) -> Result<i64, Error> {
        self.owner.complete_reaped(ticket, text)
    }

    pub fn observe_drained(&mut self, ticket: &Ticket) -> Result<i64, Error> {
        self.owner.observe_drained(ticket)
    }

    pub fn archive(&mut self, session: Id, expected: i64) -> Result<i64, Error> {
        self.owner.archive(session, expected)
    }
}
