// SPDX-License-Identifier: AGPL-3.0-only
//! Cooperative monotonic deadlines. The caller supplies trusted clock readings.
//! This module does not kill processes or prove that a worker has exited.
use std::collections::BTreeMap;
use std::sync::Arc;

const MAX_NS: u64 = 3_600_000_000_000;
const MAX_ACTIVE: usize = 64;
const MAX_ISSUED: usize = 1024;

#[derive(Debug)]
pub struct Ticket {
    identity: Arc<()>,
    sequence: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Invalid,
    Expired,
    ClockRegression,
    Limit,
    Foreign,
}

pub struct Deadlines {
    end: u64,
    worker_ns: u64,
    last: u64,
    issued: usize,
    active: BTreeMap<usize, (Arc<()>, u64)>,
    poisoned: Option<Error>,
}

impl Deadlines {
    pub fn new(now: u64, total_ns: u64, worker_ns: u64) -> Result<Self, Error> {
        if worker_ns == 0 || worker_ns > total_ns || total_ns > MAX_NS {
            return Err(Error::Invalid);
        }
        let end = now.checked_add(total_ns).ok_or(Error::Invalid)?;
        Ok(Self {
            end,
            worker_ns,
            last: now,
            issued: 0,
            active: BTreeMap::new(),
            poisoned: None,
        })
    }

    pub fn check(&mut self, now: u64) -> Result<(), Error> {
        if let Some(error) = self.poisoned {
            return Err(error);
        }
        if now < self.last {
            self.poisoned = Some(Error::ClockRegression);
            return Err(Error::ClockRegression);
        }
        self.last = now;
        if now >= self.end || self.active.values().any(|(_, end)| now >= *end) {
            self.poisoned = Some(Error::Expired);
            return Err(Error::Expired);
        }
        Ok(())
    }

    pub fn start(&mut self, now: u64) -> Result<Ticket, Error> {
        self.check(now)?;
        if self.active.len() >= MAX_ACTIVE || self.issued >= MAX_ISSUED {
            return Err(Error::Limit);
        }
        let end = now.saturating_add(self.worker_ns).min(self.end);
        self.issued += 1;
        let identity = Arc::new(());
        self.active.insert(self.issued, (identity.clone(), end));
        Ok(Ticket {
            identity,
            sequence: self.issued,
        })
    }

    fn end_of(&self, ticket: &Ticket) -> Result<u64, Error> {
        let (identity, end) = self.active.get(&ticket.sequence).ok_or(Error::Foreign)?;
        if !Arc::ptr_eq(identity, &ticket.identity) {
            return Err(Error::Foreign);
        }
        Ok(*end)
    }

    pub fn remaining(&mut self, ticket: &Ticket, now: u64) -> Result<u64, Error> {
        let end = self.end_of(ticket)?;
        self.check(now)?;
        Ok(end - now)
    }

    /// Trusted owner finishes the exact ticket, including after timeout.
    /// Finishing cannot clear a poisoned session or extend its deadline.
    pub fn finish(&mut self, ticket: &Ticket) -> Result<(), Error> {
        self.end_of(ticket)?;
        self.active.remove(&ticket.sequence);
        Ok(())
    }
}
