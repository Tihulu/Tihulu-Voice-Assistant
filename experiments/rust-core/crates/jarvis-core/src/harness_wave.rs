// SPDX-License-Identifier: AGPL-3.0-only
//! One shared synthetic Session owns the harness wave. No launch, transport,
//! process observation or permission issuance. All observations are trusted.
use crate::budget::{Amount, Demand, Snapshot};
use crate::harness::{
    self, Capability, Event, EventKey, EventTrack, Manifest, Negotiated, Phase, Protocol,
};
use crate::session::{self, Handle, Session, Terminal};
use crate::synthetic_transport::{self, Identity};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

/// Trusted owner policy snapshot identity, not a grant or authentication.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PolicyStamp {
    pub generation: u64,
    pub local_only: bool,
}

pub struct Worker<'a> {
    pub demand: Demand,
    pub plan: Negotiated,
    pub current: Current<'a>,
}

pub struct Current<'a> {
    pub manifest: &'a Manifest,
    pub allowed: &'a BTreeSet<Capability>,
    pub policy: PolicyStamp,
}

#[derive(Debug)]
pub struct WorkerKey {
    worker: String,
    identity: Arc<()>,
}

struct Row {
    identity: Arc<()>,
    handle: Handle,
    plan: Negotiated,
    track: EventTrack,
    event_key: EventKey,
    dispatched: bool,
    transport: Option<Identity>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Harness(harness::Error),
    Session(session::Error),
    Foreign,
    Phase,
    PolicyDrift,
    Transport(synthetic_transport::Error),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResultState {
    MeasuredReport,
    NoPublication,
}

pub struct HarnessWave {
    session: Session,
    rows: BTreeMap<String, Row>,
    policy: Option<PolicyStamp>,
    connections: BTreeSet<[u8; 32]>,
    owner_scope: Option<([u8; 32], [u8; 32])>,
    transport_attempt: Option<[u8; 32]>,
    seen_attempts: BTreeSet<[u8; 32]>,
    dispatch_started: bool,
}

impl HarnessWave {
    pub fn new(session: Session) -> Result<Self, Error> {
        if session.stopped() || session.snapshot().reserved != Amount::default() {
            return Err(Error::Phase);
        }
        Ok(Self {
            session,
            rows: BTreeMap::new(),
            policy: None,
            connections: BTreeSet::new(),
            owner_scope: None,
            transport_attempt: None,
            seen_attempts: BTreeSet::new(),
            dispatch_started: false,
        })
    }

    pub fn snapshot(&self) -> Snapshot {
        self.session.snapshot()
    }

    pub fn stopped(&self) -> bool {
        self.session.stopped()
    }

    pub fn drained(&self) -> bool {
        self.session.drained() && self.rows.is_empty()
    }

    pub fn reserve(
        &mut self,
        attempt: &str,
        workers: Vec<Worker<'_>>,
        now: u64,
    ) -> Result<Vec<WorkerKey>, Error> {
        if !self.rows.is_empty() {
            return Err(Error::Phase);
        }
        // All peers must pass the same trusted policy generation and their
        // individual manifest/capability preflight before any lease is issued.
        if let Some(first) = workers.first() {
            let stamp = first.current.policy;
            self.observe_policy(stamp)?;
            for worker in &workers {
                if worker.current.policy != stamp {
                    self.stop();
                    return Err(Error::PolicyDrift);
                }
                if let Err(error) = worker.plan.recheck(
                    worker.current.manifest,
                    worker.current.allowed,
                    stamp.local_only,
                ) {
                    self.stop();
                    return Err(Error::Harness(error));
                }
            }
        }
        let demands: Vec<_> = workers.iter().map(|w| w.demand.clone()).collect();
        let handles = self
            .session
            .reserve_wave(attempt, &demands, now)
            .map_err(Error::Session)?;
        self.transport_attempt = None;
        self.dispatch_started = false;
        let mut keys = Vec::with_capacity(handles.len());
        for (worker, handle) in workers.into_iter().zip(handles) {
            let identity = Arc::new(());
            let (track, event_key) = EventTrack::new();
            let name = worker.demand.worker;
            self.rows.insert(
                name.clone(),
                Row {
                    identity: identity.clone(),
                    handle,
                    plan: worker.plan,
                    track,
                    event_key,
                    dispatched: false,
                    transport: None,
                },
            );
            keys.push(WorkerKey {
                worker: name,
                identity,
            });
        }
        Ok(keys)
    }

    fn row(&self, key: &WorkerKey) -> Result<&Row, Error> {
        let row = self.rows.get(&key.worker).ok_or(Error::Foreign)?;
        if !Arc::ptr_eq(&row.identity, &key.identity) {
            return Err(Error::Foreign);
        }
        Ok(row)
    }

    pub fn stop(&mut self) {
        self.session.stop();
        for row in self.rows.values_mut() {
            row.track.cancel();
        }
    }

    pub fn resize_memory(&mut self, ram: u64, vram: u64) -> Result<(), Error> {
        self.session
            .resize_memory(ram, vram)
            .map_err(Error::Session)?;
        let snapshot = self.session.snapshot();
        if snapshot.reserved.ram > snapshot.capacity.ram
            || snapshot.reserved.vram > snapshot.capacity.vram
        {
            // A valid capacity reduction is not an exit observation.
            // Retain all reservations until exact terminal cleanup.
            self.stop();
        }
        Ok(())
    }

    /// Trusted owner heartbeat, including when no adapter events arrive.
    /// Cooperative only: stopping never establishes process exit.
    pub fn tick(&mut self, now: u64, policy: PolicyStamp) -> Result<(), Error> {
        if self.stopped() {
            return Err(Error::Session(session::Error::Closed));
        }
        self.observe_policy(policy)?;
        if let Err(error) = self.session.checkpoint(now) {
            self.stop();
            return Err(Error::Session(error));
        }
        Ok(())
    }

    fn observe_policy(&mut self, current: PolicyStamp) -> Result<(), Error> {
        let invalid = current.generation == 0
            || self.policy.is_some_and(|old| {
                current.generation < old.generation
                    || (current.generation == old.generation && current != old)
                    || (!self.rows.is_empty() && current != old)
            });
        if invalid {
            self.stop();
            return Err(Error::PolicyDrift);
        }
        self.policy = Some(current);
        Ok(())
    }

    fn check(&mut self, key: &WorkerKey, current: &Current<'_>) -> Result<(), Error> {
        self.row(key)?;
        self.observe_policy(current.policy)?;
        let result = self.row(key)?.plan.recheck(
            current.manifest,
            current.allowed,
            current.policy.local_only,
        );
        if let Err(error) = result {
            self.stop();
            return Err(Error::Harness(error));
        }
        Ok(())
    }

    /// Trusted owner binds a reviewed internal transport before dispatch.
    /// A connection identity is never reused during this wave owner's life.
    pub fn bind_transport(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        identity: Identity,
        now: u64,
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if self.dispatch_started || row.dispatched || row.transport.is_some() {
            return Err(Error::Phase);
        }
        self.check(key, current)?;
        self.tick(now, current.policy)?;
        let scope = (identity.project, identity.session);
        if !identity.valid()
            || !matches!(current.manifest.protocol, Protocol::AcpV1 | Protocol::AcpV2)
            || self.connections.contains(&identity.connection)
            || self.owner_scope.is_some_and(|old| old != scope)
            || self
                .transport_attempt
                .is_some_and(|old| old != identity.attempt)
            || (self.transport_attempt.is_none() && self.seen_attempts.contains(&identity.attempt))
            || self.rows.values().any(|row| {
                row.transport
                    .is_some_and(|old| old.worker == identity.worker)
            })
        {
            self.stop();
            return Err(Error::Transport(synthetic_transport::Error::Identity));
        }
        self.owner_scope = Some(scope);
        self.transport_attempt = Some(identity.attempt);
        self.seen_attempts.insert(identity.attempt);
        self.connections.insert(identity.connection);
        self.rows
            .get_mut(&key.worker)
            .ok_or(Error::Foreign)?
            .transport = Some(identity);
        Ok(())
    }

    /// Bound adapters cannot bypass the fixed envelope with raw events.
    pub fn record_frame(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        bytes: &[u8],
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if !row.dispatched {
            return Err(Error::Phase);
        }
        let identity = row.transport.ok_or(Error::Phase)?;
        self.check(key, current)?;
        self.tick(now, current.policy)?;
        let frame = match synthetic_transport::decode(bytes) {
            Ok(frame) => frame,
            Err(error) => {
                self.stop();
                return Err(Error::Transport(error));
            }
        };
        if frame.identity != identity || frame.protocol != current.manifest.protocol {
            self.stop();
            return Err(Error::Transport(synthetic_transport::Error::Identity));
        }
        self.record_event(key, current, now, frame.sequence, frame.event)
    }

    /// Connection loss is an uncertain outcome, not trusted process exit.
    pub fn disconnect_transport(
        &mut self,
        key: &WorkerKey,
        identity: Identity,
    ) -> Result<(), Error> {
        if self.row(key)?.transport != Some(identity) {
            return Err(Error::Foreign);
        }
        self.stop();
        Ok(())
    }

    pub fn dispatch(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
    ) -> Result<(), Error> {
        if self.row(key)?.dispatched {
            return Err(Error::Phase);
        }
        self.check(key, current)?;
        if self.rows.values().any(|row| {
            matches!(row.plan.protocol(), Protocol::AcpV1 | Protocol::AcpV2)
                && row.transport.is_none()
        }) {
            self.stop();
            return Err(Error::Phase);
        }
        let row = self.rows.get_mut(&key.worker).ok_or(Error::Foreign)?;
        if let Err(error) = self.session.dispatch(&row.handle, now) {
            self.stop();
            return Err(Error::Session(error));
        }
        row.dispatched = true;
        self.dispatch_started = true;
        Ok(())
    }

    pub fn record(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        sequence: u64,
        event: Event,
    ) -> Result<(), Error> {
        if self.row(key)?.transport.is_some() {
            return Err(Error::Phase);
        }
        self.record_event(key, current, now, sequence, event)
    }

    fn record_event(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        sequence: u64,
        event: Event,
    ) -> Result<(), Error> {
        if !self.row(key)?.dispatched {
            return Err(Error::Phase);
        }
        self.check(key, current)?;
        if let Err(error) = self.session.checkpoint(now) {
            self.stop();
            return Err(Error::Session(error));
        }
        let row = self.rows.get_mut(&key.worker).ok_or(Error::Foreign)?;
        if let Err(error) = row.track.record(&row.event_key, sequence, event) {
            self.stop();
            return Err(Error::Harness(error));
        }
        // No approval/resume authority yet. Approval requests and failures
        // stop the wave rather than silently allowing the requested effect.
        if matches!(event, Event::ApprovalNeeded | Event::ReportedFailure) {
            self.stop();
        }
        Ok(())
    }

    /// Trusted terminal observation only. Not proof of process exit, and
    /// MeasuredReport is diagnostic rather than publication permission.
    pub fn settle(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        measured: Option<(u64, u64)>,
    ) -> Result<ResultState, Error> {
        if !self.row(key)?.dispatched {
            return Err(Error::Phase);
        }
        let _ = self.check(key, current);
        if self.row(key)?.track.phase() != Phase::Draining {
            self.stop();
        }
        let row = self.rows.get_mut(&key.worker).ok_or(Error::Foreign)?;
        let terminal = self
            .session
            .settle_reaped(&row.handle, now, measured)
            .map_err(Error::Session)?;
        let outcome = row
            .track
            .observe_drained(&row.event_key)
            .map_err(Error::Harness)?;
        self.rows.remove(&key.worker);
        if terminal != Terminal::Measured || outcome != harness::Outcome::ReportedSuccessAndDrained
        {
            self.stop();
            return Ok(ResultState::NoPublication);
        }
        Ok(ResultState::MeasuredReport)
    }

    /// Trusted evidence that the prepared worker never started.
    pub fn void_unstarted(&mut self, key: &WorkerKey) -> Result<(), Error> {
        if self.row(key)?.dispatched {
            return Err(Error::Phase);
        }
        let row = self.rows.get(&key.worker).ok_or(Error::Foreign)?;
        self.session
            .void_unstarted(&row.handle)
            .map_err(Error::Session)?;
        self.rows.remove(&key.worker);
        Ok(())
    }
}
