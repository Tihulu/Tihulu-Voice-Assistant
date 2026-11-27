// SPDX-License-Identifier: AGPL-3.0-only
//! Pure wave lifecycle. Terminal observations are trusted fixture inputs.
use crate::budget::{self, Amount, Demand, Ledger, Snapshot};
use crate::deadline::{self, Deadlines, Ticket};
use std::collections::BTreeMap;
use std::sync::Arc;

#[derive(Debug)]
pub struct Handle {
    identity: Arc<()>,
    sequence: u64,
}

struct Entry {
    identity: Arc<()>,
    lease: budget::Lease,
    ticket: Option<Ticket>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Budget(budget::Error),
    Deadline(deadline::Error),
    Closed,
    Busy,
    Foreign,
    WrongPhase,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Terminal {
    Measured,
    Drained,
    UnknownUsage,
}

pub struct Session {
    ledger: Ledger,
    deadlines: Deadlines,
    entries: BTreeMap<u64, Entry>,
    next: u64,
    stopped: bool,
}

impl Session {
    pub fn new(capacity: Amount, now: u64, total_ns: u64, worker_ns: u64) -> Result<Self, Error> {
        Ok(Self {
            ledger: Ledger::new(capacity).map_err(Error::Budget)?,
            deadlines: Deadlines::new(now, total_ns, worker_ns).map_err(Error::Deadline)?,
            entries: BTreeMap::new(),
            next: 0,
            stopped: false,
        })
    }

    pub fn snapshot(&self) -> Snapshot {
        self.ledger.snapshot()
    }

    pub fn stopped(&self) -> bool {
        self.stopped
    }

    pub fn drained(&self) -> bool {
        self.stopped && self.entries.is_empty()
    }

    pub fn stop(&mut self) {
        self.stopped = true;
        self.ledger.close();
    }

    pub fn checkpoint(&mut self, now: u64) -> Result<(), Error> {
        if self.stopped {
            return Err(Error::Closed);
        }
        if let Err(error) = self.deadlines.check(now) {
            self.stop();
            return Err(Error::Deadline(error));
        }
        Ok(())
    }

    pub fn reserve_wave(
        &mut self,
        attempt: &str,
        demands: &[Demand],
        now: u64,
    ) -> Result<Vec<Handle>, Error> {
        self.checkpoint(now)?;
        if !self.entries.is_empty() {
            return Err(Error::Busy);
        }
        let leases = match self.ledger.reserve_wave(attempt, demands) {
            Ok(leases) => leases,
            Err(error) => {
                self.stop();
                return Err(Error::Budget(error));
            }
        };
        let mut handles = Vec::with_capacity(leases.len());
        for lease in leases {
            // The ledger bounds total issued leases to 1024.
            self.next += 1;
            let identity = Arc::new(());
            self.entries.insert(
                self.next,
                Entry {
                    identity: identity.clone(),
                    lease,
                    ticket: None,
                },
            );
            handles.push(Handle {
                identity,
                sequence: self.next,
            });
        }
        Ok(handles)
    }

    fn entry(&self, handle: &Handle) -> Result<&Entry, Error> {
        let entry = self.entries.get(&handle.sequence).ok_or(Error::Foreign)?;
        if !Arc::ptr_eq(&entry.identity, &handle.identity) {
            return Err(Error::Foreign);
        }
        Ok(entry)
    }

    pub fn resize_memory(&mut self, ram: u64, vram: u64) -> Result<(), Error> {
        self.ledger.resize_memory(ram, vram).map_err(Error::Budget)
    }

    pub fn dispatch(&mut self, handle: &Handle, now: u64) -> Result<(), Error> {
        if self.entry(handle)?.ticket.is_some() {
            return Err(Error::WrongPhase);
        }
        self.checkpoint(now)?;
        let ticket = match self.deadlines.start(now) {
            Ok(ticket) => ticket,
            Err(error) => {
                self.stop();
                return Err(Error::Deadline(error));
            }
        };
        let entry = self
            .entries
            .get_mut(&handle.sequence)
            .ok_or(Error::Foreign)?;
        if let Err(error) = self.ledger.dispatch(&entry.lease) {
            self.deadlines.finish(&ticket).map_err(Error::Deadline)?;
            self.stop();
            return Err(Error::Budget(error));
        }
        entry.ticket = Some(ticket);
        Ok(())
    }

    pub fn remaining(&mut self, handle: &Handle, now: u64) -> Result<u64, Error> {
        if self.entry(handle)?.ticket.is_none() {
            return Err(Error::WrongPhase);
        }
        self.checkpoint(now)?;
        let entry = self.entries.get(&handle.sequence).ok_or(Error::Foreign)?;
        let ticket = entry.ticket.as_ref().ok_or(Error::WrongPhase)?;
        match self.deadlines.remaining(ticket, now) {
            Ok(value) => Ok(value),
            Err(error) => {
                self.stop();
                Err(Error::Deadline(error))
            }
        }
    }

    /// The trusted owner has observed the worker and all descendants exit.
    /// Late or unknown results never carry authority to publish output.
    pub fn settle_reaped(
        &mut self,
        handle: &Handle,
        now: u64,
        measured: Option<(u64, u64)>,
    ) -> Result<Terminal, Error> {
        if self.entry(handle)?.ticket.is_none() {
            return Err(Error::WrongPhase);
        }
        if !self.stopped {
            let _ = self.checkpoint(now);
        }
        let entry = self.entries.get(&handle.sequence).ok_or(Error::Foreign)?;
        let ticket = entry.ticket.as_ref().ok_or(Error::WrongPhase)?;
        let mut unknown = measured.is_none();
        if let Err(error) = self.ledger.settle_reaped(&entry.lease, measured) {
            if error != budget::Error::Invalid {
                return Err(Error::Budget(error));
            }
            self.ledger
                .settle_reaped(&entry.lease, None)
                .map_err(Error::Budget)?;
            unknown = true;
        }
        self.deadlines.finish(ticket).map_err(Error::Deadline)?;
        self.entries.remove(&handle.sequence);
        if unknown {
            self.stop();
            return Ok(Terminal::UnknownUsage);
        }
        if self.stopped {
            Ok(Terminal::Drained)
        } else {
            Ok(Terminal::Measured)
        }
    }

    pub fn void_unstarted(&mut self, handle: &Handle) -> Result<(), Error> {
        if !self.stopped || self.entry(handle)?.ticket.is_some() {
            return Err(Error::WrongPhase);
        }
        let entry = self.entries.get(&handle.sequence).ok_or(Error::Foreign)?;
        self.ledger
            .void_unstarted(&entry.lease)
            .map_err(Error::Budget)?;
        self.entries.remove(&handle.sequence);
        Ok(())
    }
}
