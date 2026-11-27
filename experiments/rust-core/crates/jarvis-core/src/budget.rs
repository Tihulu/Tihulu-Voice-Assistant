// SPDX-License-Identifier: AGPL-3.0-only
//! Single-owner, atomic synthetic RAM/VRAM and cumulative token accounting.
//! Terminal events must be supplied by a trusted broker; no OS effects here.
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

const MAX_ACTIVE: usize = 64;
const MAX_SEEN: usize = 1024;
const MAX_WAVE: usize = 10;
const MAX_DIM: u64 = 1_048_576;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Amount {
    pub ram: u64,
    pub vram: u64,
    pub generated: u64,
    pub prompt: u64,
}
impl Amount {
    fn valid(self) -> bool {
        [self.ram, self.vram, self.generated, self.prompt]
            .into_iter()
            .all(|n| n <= MAX_DIM)
    }
    fn add(self, b: Self) -> Option<Self> {
        Some(Self {
            ram: self.ram.checked_add(b.ram)?,
            vram: self.vram.checked_add(b.vram)?,
            generated: self.generated.checked_add(b.generated)?,
            prompt: self.prompt.checked_add(b.prompt)?,
        })
    }
    fn fits(self, b: Self) -> bool {
        self.ram <= b.ram && self.vram <= b.vram
            && self.generated <= b.generated && self.prompt <= b.prompt
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Demand {
    pub worker: String,
    pub amount: Amount,
}
#[derive(Debug)]
pub struct Lease {
    identity: Arc<()>,
    attempt: String,
    worker: String,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Phase { Prepared, Dispatched }
struct Entry {
    identity: Arc<()>,
    amount: Amount,
    phase: Phase,
    cancelled: bool,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Invalid, Exhausted, Duplicate, Limit, Closed, Foreign, WrongPhase,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Snapshot {
    pub capacity: Amount,
    pub reserved: Amount,
    pub spent: Amount,
}
pub struct Ledger {
    capacity: Amount,
    spent: Amount,
    entries: BTreeMap<(String, String), Entry>,
    seen: BTreeSet<(String, String)>,
    closed: bool,
}
fn valid_id(s: &str) -> bool {
    !s.is_empty() && s.len() <= 128
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
}
impl Ledger {
    pub fn new(capacity: Amount) -> Result<Self, Error> {
        if !capacity.valid() { return Err(Error::Invalid); }
        Ok(Self { capacity, spent: Amount::default(), entries: BTreeMap::new(),
            seen: BTreeSet::new(), closed: false })
    }
    pub fn snapshot(&self) -> Snapshot {
        // Entries are bounded to MAX_ACTIVE, each dimension <= MAX_DIM.
        let reserved = self.entries.values().fold(Amount::default(), |a, e|
            a.add(e.amount).expect("bounded reservation sum"));
        Snapshot { capacity: self.capacity, reserved, spent: self.spent }
    }
    pub fn reserve_wave(&mut self, attempt: &str, demands: &[Demand]) -> Result<Vec<Lease>, Error> {
        if self.closed { return Err(Error::Closed); }
        if !valid_id(attempt) || demands.is_empty() || demands.len() > MAX_WAVE {
            return Err(Error::Invalid);
        }
        if self.entries.len() + demands.len() > MAX_ACTIVE
            || self.seen.len() + demands.len() > MAX_SEEN { return Err(Error::Limit); }
        let mut keys = BTreeSet::new();
        let mut total = self.snapshot().reserved.add(self.spent).ok_or(Error::Exhausted)?;
        for demand in demands {
            if !valid_id(&demand.worker) || !demand.amount.valid()
                || demand.amount.ram == 0 || demand.amount.generated == 0
                || demand.amount.prompt == 0 { return Err(Error::Invalid); }
            let key = (attempt.to_owned(), demand.worker.clone());
            if self.seen.contains(&key) || !keys.insert(key)
                || self.entries.keys().any(|(_, worker)| worker == &demand.worker) {
                return Err(Error::Duplicate);
            }
            total = total.add(demand.amount).ok_or(Error::Exhausted)?;
        }
        if !total.fits(self.capacity) { return Err(Error::Exhausted); }
        let mut leases = Vec::with_capacity(demands.len());
        for demand in demands {
            let key = (attempt.to_owned(), demand.worker.clone());
            let identity = Arc::new(());
            self.seen.insert(key.clone());
            self.entries.insert(key.clone(), Entry { identity: identity.clone(), amount: demand.amount,
                phase: Phase::Prepared, cancelled: false });
            leases.push(Lease { identity, attempt: key.0, worker: key.1 });
        }
        Ok(leases)
    }
    fn entry(&mut self, lease: &Lease) -> Result<&mut Entry, Error> {
        let entry = self.entries.get_mut(&(lease.attempt.clone(), lease.worker.clone()))
            .ok_or(Error::Foreign)?;
        if !Arc::ptr_eq(&entry.identity, &lease.identity) { return Err(Error::Foreign); }
        Ok(entry)
    }
    pub fn dispatch(&mut self, lease: &Lease) -> Result<(), Error> {
        if self.closed { return Err(Error::Closed); }
        let e = self.entry(lease)?;
        if e.cancelled || e.phase != Phase::Prepared { return Err(Error::WrongPhase); }
        e.phase = Phase::Dispatched;
        Ok(())
    }
    pub fn cancel(&mut self, lease: &Lease) -> Result<(), Error> {
        self.entry(lease)?.cancelled = true;
        Ok(())
    }
    pub fn close(&mut self) {
        self.closed = true;
        for e in self.entries.values_mut() { e.cancelled = true; }
    }
    pub fn resize_memory(&mut self, ram: u64, vram: u64) -> Result<(), Error> {
        if self.closed { return Err(Error::Closed); }
        if ram > MAX_DIM || vram > MAX_DIM { return Err(Error::Invalid); }
        self.capacity.ram = ram;
        self.capacity.vram = vram;
        Ok(())
    }
    pub fn void_unstarted(&mut self, lease: &Lease) -> Result<(), Error> {
        let e = self.entry(lease)?;
        if !e.cancelled || e.phase != Phase::Prepared { return Err(Error::WrongPhase); }
        self.entries.remove(&(lease.attempt.clone(), lease.worker.clone()));
        Ok(())
    }
    /// Trusted terminal observation: unknown usage charges both full ceilings.
    /// Cancellation alone is never a terminal observation.
    pub fn settle_reaped(&mut self, lease: &Lease, measured: Option<(u64, u64)>) -> Result<(), Error> {
        let e = self.entry(lease)?;
        if e.phase != Phase::Dispatched { return Err(Error::WrongPhase); }
        let (generated, prompt) = measured.unwrap_or((e.amount.generated, e.amount.prompt));
        if generated > e.amount.generated || prompt > e.amount.prompt { return Err(Error::Invalid); }
        let charge = Amount { generated, prompt, ..Amount::default() };
        let spent = self.spent.add(charge).ok_or(Error::Exhausted)?;
        self.entries.remove(&(lease.attempt.clone(), lease.worker.clone()));
        self.spent = spent;
        Ok(())
    }
}
