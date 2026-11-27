// SPDX-License-Identifier: AGPL-3.0-only
//! Trusted server adapter, not a network authentication implementation.
//! Grant issuance and stable client IDs must come from an authenticated owner.
use crate::exclusive::ExclusiveStore;
use crate::owned::{Admission, Ticket};
use crate::{Error, Id, Message};
use std::collections::BTreeMap;
use std::sync::Arc;

const MAX_GRANTS: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Mode {
    Read,
    Chat,
}

#[derive(Debug)]
pub struct Grant {
    identity: Arc<()>,
    session: Id,
    client: Id,
}

struct Row {
    identity: Arc<()>,
    mode: Mode,
}

pub struct ScopedStore {
    store: ExclusiveStore,
    grants: BTreeMap<(Id, Id), Row>,
}

impl ScopedStore {
    pub fn new(store: ExclusiveStore) -> Self {
        Self {
            store,
            grants: BTreeMap::new(),
        }
    }

    /// Trusted owner only. A client identifier is not a credential.
    pub fn grant(&mut self, session: Id, client: Id, mode: Mode) -> Result<Grant, Error> {
        self.store.history(session)?;
        if self.grants.contains_key(&(session, client)) {
            return Err(Error::Conflict);
        }
        if self.grants.len() >= MAX_GRANTS {
            return Err(Error::Limit);
        }
        let identity = Arc::new(());
        self.grants.insert(
            (session, client),
            Row {
                identity: identity.clone(),
                mode,
            },
        );
        Ok(Grant {
            identity,
            session,
            client,
        })
    }

    fn check(&self, grant: &Grant, write: bool) -> Result<(), Error> {
        let row = self
            .grants
            .get(&(grant.session, grant.client))
            .ok_or(Error::Conflict)?;
        if !Arc::ptr_eq(&row.identity, &grant.identity) || (write && row.mode != Mode::Chat) {
            return Err(Error::Conflict);
        }
        Ok(())
    }

    /// Owner-approved transport handoff; old capability becomes invalid.
    pub fn rotate(&mut self, grant: &Grant) -> Result<Grant, Error> {
        self.check(grant, false)?;
        let row = self
            .grants
            .get_mut(&(grant.session, grant.client))
            .ok_or(Error::Conflict)?;
        let identity = Arc::new(());
        row.identity = identity.clone();
        Ok(Grant {
            identity,
            session: grant.session,
            client: grant.client,
        })
    }

    /// Revocation denies subsequent client requests. It does not assert that
    /// a previously approved worker has exited. Runtime stop is separate.
    pub fn revoke(&mut self, grant: &Grant) -> Result<(), Error> {
        self.check(grant, false)?;
        self.grants.remove(&(grant.session, grant.client));
        Ok(())
    }

    pub fn history(&self, grant: &Grant) -> Result<(i64, Vec<Message>), Error> {
        self.check(grant, false)?;
        self.store.history(grant.session)
    }

    /// The trusted dispatcher receives Admission, not the remote client.
    /// The session/client namespace is derived solely from the grant.
    pub fn submit(
        &mut self,
        grant: &Grant,
        expected: i64,
        key: &str,
        text: &str,
    ) -> Result<Admission, Error> {
        self.check(grant, true)?;
        self.store
            .submit(grant.session, grant.client, expected, key, text)
    }

    // Runtime-only operations. The transport must never expose tickets.
    pub fn complete_reaped(&mut self, ticket: &Ticket, text: &str) -> Result<i64, Error> {
        self.store.complete_reaped(ticket, text)
    }

    pub fn cancel(&mut self, ticket: &Ticket, expected: i64) -> Result<i64, Error> {
        self.store.cancel(ticket, expected)
    }

    pub fn observe_drained(&mut self, ticket: &Ticket) -> Result<i64, Error> {
        self.store.observe_drained(ticket)
    }
}
