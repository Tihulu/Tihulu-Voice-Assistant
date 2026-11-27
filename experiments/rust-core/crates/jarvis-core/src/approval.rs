// SPDX-License-Identifier: AGPL-3.0-only
//! Pure approval matching fixture. No real authorization, UI, IO or resume.
//! The owner must supply trusted identity, canonical digests and clock.
use crate::harness_wave::PolicyStamp;
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
