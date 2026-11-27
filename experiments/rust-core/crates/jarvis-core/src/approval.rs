// SPDX-License-Identifier: AGPL-3.0-only
//! Pure approval matching fixture. No real authorization, UI, IO or resume.
//! The owner must supply trusted identity, canonical digests and clock.
use crate::harness_wave::PolicyStamp;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

const MAX_LIFETIME: u64 = 3_600_000_000_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Binding {
    // Canonical digest of project/session/turn/attempt/worker identity.
    pub subject: [u8; 32],
    // Canonical digest of exact typed action, arguments and target.
    pub action: [u8; 32],
    pub policy: PolicyStamp,
    pub expires_at: u64,
}

#[derive(Debug)]
pub struct Receipt(Arc<()>);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Invalid,
    Foreign,
    Drift,
    Expired,
    ClockRegression,
    State,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Phase {
    Pending,
    Approved,
    Consumed,
    Closed,
}

pub struct Gate {
    binding: Binding,
    identity: Arc<()>,
    last: u64,
    phase: Phase,
}

impl Gate {
    pub fn new(binding: Binding, now: u64) -> Result<Self, Error> {
        if binding.subject == [0; 32]
            || binding.action == [0; 32]
            || binding.policy.generation == 0
            || binding.expires_at <= now
            || binding.expires_at - now > MAX_LIFETIME
        {
            return Err(Error::Invalid);
        }
        Ok(Self {
            binding,
            identity: Arc::new(()),
            last: now,
            phase: Phase::Pending,
        })
    }

    pub fn phase(&self) -> Phase {
        self.phase
    }

    pub fn revoke(&mut self) {
        if self.phase != Phase::Consumed {
            self.phase = Phase::Closed;
        }
    }

    fn check(&mut self, observed: &Binding, now: u64) -> Result<(), Error> {
        let error = if now < self.last {
            Some(Error::ClockRegression)
        } else if now >= self.binding.expires_at {
            Some(Error::Expired)
        } else if *observed != self.binding {
            Some(Error::Drift)
        } else {
            None
        };
        if let Some(error) = error {
            self.revoke();
            return Err(error);
        }
        self.last = now;
        Ok(())
    }

    /// Called only for a trusted, explicit affirmative owner decision.
    pub fn approve(&mut self, observed: &Binding, now: u64) -> Result<Receipt, Error> {
        if self.phase != Phase::Pending {
            return Err(Error::State);
        }
        self.check(observed, now)?;
        self.phase = Phase::Approved;
        Ok(Receipt(self.identity.clone()))
    }

    /// Check again at the proposed effect boundary. A match is consumed
    /// before any future effect; failure/unknown outcome never resets it.
    /// This is a synthetic match, not permission to perform a host action.
    pub fn consume(
        &mut self,
        receipt: &Receipt,
        observed: &Binding,
        now: u64,
    ) -> Result<(), Error> {
        if !Arc::ptr_eq(&self.identity, &receipt.0) {
            return Err(Error::Foreign);
        }
        if self.phase != Phase::Approved {
            return Err(Error::State);
        }
        self.check(observed, now)?;
        self.phase = Phase::Consumed;
        Ok(())
    }
}

const MAX_REQUESTS: usize = 1024;
type Digest = [u8; 32];

#[derive(Debug)]
pub struct RequestKey {
    request: Digest,
    owner: Arc<()>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RegistryError {
    Gate(Error),
    Foreign,
    Duplicate,
    Limit,
    Closed,
    PolicyDrift,
}

/// One trusted owner, one in-memory registry for its entire lifetime.
/// Never evict tombstones. Digests and policy observations are trusted inputs,
/// not authenticated or canonicalized here. No cross-restart guarantee.
pub struct Registry {
    owner: Arc<()>,
    policy: PolicyStamp,
    last: u64,
    closed: bool,
    rows: BTreeMap<Digest, Gate>,
    operations: BTreeSet<(Digest, Digest)>,
}

impl Registry {
    pub fn new(policy: PolicyStamp, now: u64) -> Result<Self, RegistryError> {
        if policy.generation == 0 {
            return Err(RegistryError::PolicyDrift);
        }
        Ok(Self {
            owner: Arc::new(()),
            policy,
            last: now,
            closed: false,
            rows: BTreeMap::new(),
            operations: BTreeSet::new(),
        })
    }

    pub fn closed(&self) -> bool {
        self.closed
    }

    pub fn close(&mut self) {
        self.closed = true;
        self.revoke_all();
    }

    fn revoke_all(&mut self) {
        for gate in self.rows.values_mut() {
            gate.revoke();
        }
    }

    /// Explicit trusted heartbeat and policy observation. A new generation
    /// revokes old decisions but never clears request or operation history.
    pub fn observe(&mut self, policy: PolicyStamp, now: u64) -> Result<(), RegistryError> {
        if self.closed {
            return Err(RegistryError::Closed);
        }
        if now < self.last {
            self.close();
            return Err(RegistryError::Gate(Error::ClockRegression));
        }
        if policy.generation < self.policy.generation
            || (policy.generation == self.policy.generation && policy != self.policy)
        {
            self.close();
            return Err(RegistryError::PolicyDrift);
        }
        if policy != self.policy {
            self.revoke_all();
            self.policy = policy;
        }
        self.last = now;
        for gate in self.rows.values_mut() {
            if now >= gate.binding.expires_at {
                gate.revoke();
            }
        }
        Ok(())
    }

    pub fn register(
        &mut self,
        request: Digest,
        binding: Binding,
        policy: PolicyStamp,
        now: u64,
    ) -> Result<RequestKey, RegistryError> {
        self.observe(policy, now)?;
        if request == [0; 32] {
            return Err(RegistryError::Gate(Error::Invalid));
        }
        if binding.policy != self.policy {
            return Err(RegistryError::PolicyDrift);
        }
        let operation = (binding.subject, binding.action);
        if self.rows.contains_key(&request) || self.operations.contains(&operation) {
            return Err(RegistryError::Duplicate);
        }
        if self.rows.len() >= MAX_REQUESTS {
            return Err(RegistryError::Limit);
        }
        let gate = Gate::new(binding, now).map_err(RegistryError::Gate)?;
        self.rows.insert(request, gate);
        self.operations.insert(operation);
        Ok(RequestKey {
            request,
            owner: self.owner.clone(),
        })
    }

    fn exact(&self, key: &RequestKey) -> Result<&Gate, RegistryError> {
        if !Arc::ptr_eq(&self.owner, &key.owner) {
            return Err(RegistryError::Foreign);
        }
        self.rows.get(&key.request).ok_or(RegistryError::Foreign)
    }

    pub fn phase(&self, key: &RequestKey) -> Result<Phase, RegistryError> {
        Ok(self.exact(key)?.phase())
    }

    pub fn revoke(&mut self, key: &RequestKey) -> Result<(), RegistryError> {
        self.exact(key)?;
        self.rows
            .get_mut(&key.request)
            .ok_or(RegistryError::Foreign)?
            .revoke();
        Ok(())
    }

    pub fn approve(
        &mut self,
        key: &RequestKey,
        observed: &Binding,
        policy: PolicyStamp,
        now: u64,
    ) -> Result<Receipt, RegistryError> {
        self.exact(key)?;
        self.observe(policy, now)?;
        self.rows
            .get_mut(&key.request)
            .ok_or(RegistryError::Foreign)?
            .approve(observed, now)
            .map_err(RegistryError::Gate)
    }

    pub fn consume(
        &mut self,
        key: &RequestKey,
        receipt: &Receipt,
        observed: &Binding,
        policy: PolicyStamp,
        now: u64,
    ) -> Result<(), RegistryError> {
        let gate = self.exact(key)?;
        if !Arc::ptr_eq(&gate.identity, &receipt.0) {
            return Err(RegistryError::Foreign);
        }
        self.observe(policy, now)?;
        self.rows
            .get_mut(&key.request)
            .ok_or(RegistryError::Foreign)?
            .consume(receipt, observed, now)
            .map_err(RegistryError::Gate)
    }
}
