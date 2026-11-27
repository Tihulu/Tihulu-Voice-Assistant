// SPDX-License-Identifier: AGPL-3.0-only
//! Typed, owner-scoped synthetic approval broker. No execution or resume.
//! Identity, manifest, digests and policy are trusted owner observations.
use crate::approval::{Binding, Phase, Receipt, Registry, RegistryError, RequestKey};
use crate::harness::{self, Capability, Manifest};
use crate::harness_wave::PolicyStamp;
use std::collections::{BTreeMap, BTreeSet};

type Id = [u8; 32];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Scope {
    pub project: Id,
    pub session: Id,
    pub turn: Id,
    pub attempt: Id,
    pub worker: Id,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Action {
    Plan { target: Id, input: Id },
    HostTask { target: Id, input: Id },
}

impl Action {
    fn capability(self) -> Capability {
        match self {
            Self::Plan { .. } => Capability::Propose,
            Self::HostTask { .. } => Capability::HostShell,
        }
    }

    fn valid(self) -> bool {
        let (target, input) = match self {
            Self::Plan { target, input } | Self::HostTask { target, input } => (target, input),
        };
        target != [0; 32] && input != [0; 32]
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Request {
    pub scope: Scope,
    pub action: Action,
    pub binding: Binding,
    pub manifest: Manifest,
}

pub struct Observation<'a> {
    pub request: &'a Request,
    pub allowed: &'a BTreeSet<Capability>,
    pub policy: PolicyStamp,
}

#[derive(Debug)]
pub struct Key {
    id: Id,
    receipt_key: RequestKey,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Registry(RegistryError),
    Harness(harness::Error),
    Scope,
    Drift,
    Duplicate,
}

pub struct Broker {
    project: Id,
    session: Id,
    registry: Registry,
    requests: BTreeMap<Id, Request>,
}

impl Broker {
    pub fn new(project: Id, session: Id, policy: PolicyStamp, now: u64) -> Result<Self, Error> {
        if project == [0; 32] || session == [0; 32] {
            return Err(Error::Scope);
        }
        Ok(Self {
            project,
            session,
            registry: Registry::new(policy, now).map_err(Error::Registry)?,
            requests: BTreeMap::new(),
        })
    }

    pub fn observe(&mut self, policy: PolicyStamp, now: u64) -> Result<(), Error> {
        self.registry.observe(policy, now).map_err(Error::Registry)
    }

    fn validate(&self, request: &Request, observed: &Observation<'_>) -> Result<(), Error> {
        let scope = request.scope;
        if scope.project != self.project
            || scope.session != self.session
            || [scope.turn, scope.attempt, scope.worker].contains(&[0; 32])
            || !request.action.valid()
        {
            return Err(Error::Scope);
        }
        if request != observed.request || request.binding.policy != observed.policy {
            return Err(Error::Drift);
        }
        let requested = BTreeSet::from([request.action.capability()]);
        harness::negotiate(
            &request.manifest,
            &observed.request.manifest,
            observed.allowed,
            &requested,
            observed.policy.local_only,
        )
        .map_err(Error::Harness)?;
        Ok(())
    }

    pub fn register(
        &mut self,
        id: Id,
        request: &Request,
        observed: &Observation<'_>,
        now: u64,
    ) -> Result<Key, Error> {
        self.observe(observed.policy, now)?;
        self.validate(request, observed)?;
        // The registry remains the only receipt authority. Typed metadata
        // also detects a duplicate even if caller-provided digests change.
        if self
            .requests
            .values()
            .any(|old| old.scope == request.scope && old.action == request.action)
        {
            return Err(Error::Duplicate);
        }
        let receipt_key = self
            .registry
            .register(id, request.binding, observed.policy, now)
            .map_err(Error::Registry)?;
        self.requests.insert(id, request.clone());
        Ok(Key { id, receipt_key })
    }

    pub fn phase(&self, key: &Key) -> Result<Phase, Error> {
        self.registry
            .phase(&key.receipt_key)
            .map_err(Error::Registry)
    }

    pub fn revoke(&mut self, key: &Key) -> Result<(), Error> {
        self.registry
            .revoke(&key.receipt_key)
            .map_err(Error::Registry)
    }

    fn recheck(&mut self, key: &Key, observed: &Observation<'_>, now: u64) -> Result<(), Error> {
        self.phase(key)?;
        self.observe(observed.policy, now)?;
        let result = self.validate(self.requests.get(&key.id).ok_or(Error::Scope)?, observed);
        if let Err(error) = result {
            self.revoke(key)?;
            return Err(error);
        }
        Ok(())
    }

    pub fn approve(
        &mut self,
        key: &Key,
        observed: &Observation<'_>,
        now: u64,
    ) -> Result<Receipt, Error> {
        self.recheck(key, observed, now)?;
        self.registry
            .approve(
                &key.receipt_key,
                &observed.request.binding,
                observed.policy,
                now,
            )
            .map_err(Error::Registry)
    }

    /// Consumes the exact synthetic receipt; performs no actual effect.
    pub fn consume(
        &mut self,
        key: &Key,
        receipt: &Receipt,
        observed: &Observation<'_>,
        now: u64,
    ) -> Result<(), Error> {
        self.registry
            .matches_receipt(&key.receipt_key, receipt)
            .map_err(Error::Registry)?;
        self.recheck(key, observed, now)?;
        self.registry
            .consume(
                &key.receipt_key,
                receipt,
                &observed.request.binding,
                observed.policy,
                now,
            )
            .map_err(Error::Registry)
    }
}
