// SPDX-License-Identifier: AGPL-3.0-only
//! Trusted-owner bridge. A persistent receipt is not an execution capability.
//! Terminal observations still require a real, separately verified runtime.
use crate::{Error, Id, Message, Receipt, Recovery, Store};
use std::collections::BTreeMap;
use std::sync::Arc;

const MAX_ACTIVE: usize = 64;

#[derive(Debug)]
pub struct Ticket {
    identity: Arc<()>,
    session: Id,
}

#[derive(Debug)]
pub enum Admission {
    New(Receipt, Ticket),
    Replay(Receipt),
}

struct Entry {
    identity: Arc<()>,
    client: Id,
    key: String,
    recovery_only: bool,
}

pub struct OwnedStore {
    store: Store,
    active: BTreeMap<Id, Entry>,
}

impl OwnedStore {
    pub fn new(store: Store) -> Self {
        Self {
            store,
            active: BTreeMap::new(),
        }
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn backup_idle(&self, destination: &std::path::Path) -> Result<(), Error> {
        if !self.active.is_empty() {
            return Err(Error::Busy);
        }
        self.store.backup_idle(destination)
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn project(&self, id: Id, name: &str) -> Result<(), Error> {
        self.store.project(id, name)
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn session(&self, id: Id, project: Option<Id>) -> Result<(), Error> {
        self.store.session(id, project)
    }

    pub fn history(&self, session: Id) -> Result<(i64, Vec<Message>), Error> {
        self.store.history(session)
    }

    pub fn recovery(&self, session: Id) -> Result<Option<Recovery>, Error> {
        self.store.recovery(session)
    }

    fn entry(&self, ticket: &Ticket) -> Result<&Entry, Error> {
        let entry = self.active.get(&ticket.session).ok_or(Error::Unknown)?;
        if !Arc::ptr_eq(&entry.identity, &ticket.identity) {
            return Err(Error::Conflict);
        }
        Ok(entry)
    }

    fn issue(&mut self, session: Id, client: Id, key: &str, recovery_only: bool) -> Ticket {
        let identity = Arc::new(());
        self.active.insert(
            session,
            Entry {
                identity: identity.clone(),
                client,
                key: key.to_owned(),
                recovery_only,
            },
        );
        Ticket { identity, session }
    }

    pub fn submit(
        &mut self,
        session: Id,
        client: Id,
        expected: i64,
        key: &str,
        text: &str,
    ) -> Result<Admission, Error> {
        // Never create a durable pending row that cannot be tracked locally.
        if self.active.len() >= MAX_ACTIVE && !self.active.contains_key(&session) {
            return Err(Error::Limit);
        }
        if let Some(entry) = self.active.get(&session) {
            if entry.client != client || entry.key != key {
                return Err(Error::Busy);
            }
        }
        let receipt = self.store.submit(session, client, expected, key, text)?;
        if receipt.replay {
            return Ok(Admission::Replay(receipt));
        }
        let ticket = self.issue(session, client, key, false);
        Ok(Admission::New(receipt, ticket))
    }

    /// Recovery is cancellation-only, never re-dispatch or publication.
    /// The trusted caller must have exclusive recovery authority for this DB.
    pub fn recover_cancel(&mut self, session: Id, expected: i64) -> Result<Ticket, Error> {
        if self.active.contains_key(&session) {
            return Err(Error::Busy);
        }
        if self.active.len() >= MAX_ACTIVE {
            return Err(Error::Limit);
        }
        let recovery = self.store.recover_cancel(session, expected)?;
        Ok(self.issue(session, recovery.client, &recovery.key, true))
    }

    pub fn cancel(&mut self, ticket: &Ticket, expected: i64) -> Result<i64, Error> {
        let entry = self.entry(ticket)?;
        let client = entry.client;
        let key = entry.key.clone();
        self.store.cancel(ticket.session, client, &key, expected)
    }

    /// Only a trusted runtime may supply a verified terminal observation.
    pub fn complete_reaped(&mut self, ticket: &Ticket, text: &str) -> Result<i64, Error> {
        let entry = self.entry(ticket)?;
        if entry.recovery_only {
            return Err(Error::Conflict);
        }
        let client = entry.client;
        let key = entry.key.clone();
        let result = self.store.complete(ticket.session, client, &key, text)?;
        self.active.remove(&ticket.session);
        Ok(result)
    }

    /// Cancellation is not exit evidence; the caller must verify owned drain.
    pub fn observe_drained(&mut self, ticket: &Ticket) -> Result<i64, Error> {
        let entry = self.entry(ticket)?;
        let client = entry.client;
        let key = entry.key.clone();
        let result = self.store.drain(ticket.session, client, &key)?;
        self.active.remove(&ticket.session);
        Ok(result)
    }

    pub fn archive(&mut self, session: Id, expected: i64) -> Result<i64, Error> {
        if self.active.contains_key(&session) {
            return Err(Error::Busy);
        }
        self.store.archive(session, expected)
    }
}
