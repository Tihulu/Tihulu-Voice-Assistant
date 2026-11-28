// SPDX-License-Identifier: AGPL-3.0-only
//! One shared synthetic Session owns the harness wave. No launch, transport,
//! process observation or permission issuance. All observations are trusted.
use crate::acp_v1::{self, Adapter, CancelPlan, Ingress};
use crate::budget::{Amount, Demand, Snapshot};
use crate::harness::{
    self, Capability, Event, EventKey, EventTrack, Manifest, Negotiated, Phase, Protocol,
};
use crate::session::{self, Handle, Session, Terminal};
use crate::synthetic_transport::{self, Identity, FRAME_LEN, MAX_CHUNK};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

type TransportId = [u8; 32];
type TransportHistory = (Option<TransportId>, BTreeSet<TransportId>);

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
    approval_requested: bool,
    planned: Option<Identity>,
    transport: Option<Identity>,
    acp_instance: Option<Arc<()>>,
    pending: [u8; FRAME_LEN],
    pending_len: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Harness(harness::Error),
    Acp(acp_v1::Error),
    Session(session::Error),
    Foreign,
    Phase,
    PolicyDrift,
    DuplicateAttempt,
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
    seen_attempts: BTreeSet<[u8; 32]>,
    seen_attempt_labels: BTreeSet<String>,
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
            seen_attempts: BTreeSet::new(),
            seen_attempt_labels: BTreeSet::new(),
            dispatch_started: false,
        })
    }

    /// Owner preconfigures the project and session independently of adapters.
    pub fn new_scoped(
        session: Session,
        project: [u8; 32],
        session_id: [u8; 32],
    ) -> Result<Self, Error> {
        if project == [0; 32] || session_id == [0; 32] {
            return Err(Error::Transport(synthetic_transport::Error::Identity));
        }
        let mut wave = Self::new(session)?;
        wave.owner_scope = Some((project, session_id));
        Ok(wave)
    }

    pub fn snapshot(&self) -> Snapshot {
        self.session.snapshot()
    }

    pub fn stopped(&self) -> bool {
        self.session.stopped()
    }

    /// No prepared or dispatched rows remain; unlike drained, idle need not be stopped.
    pub fn idle(&self) -> bool {
        self.rows.is_empty() && self.session.snapshot().reserved == Amount::default()
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
        self.reserve_with_transport(attempt, workers, BTreeMap::new(), now)
    }

    /// The trusted owner predeclares exact transport IDs for every ACP-labelled
    /// worker before acquiring any lease. Non-ACP workers must not have IDs.
    pub fn reserve_with_transport(
        &mut self,
        attempt: &str,
        workers: Vec<Worker<'_>>,
        planned: BTreeMap<String, Identity>,
        now: u64,
    ) -> Result<Vec<WorkerKey>, Error> {
        if !self.rows.is_empty() {
            return Err(Error::Phase);
        }
        // A wave label identifies one admission, even if a later request
        // changes every worker or transport digest.
        if self.seen_attempt_labels.contains(attempt) {
            self.stop();
            return Err(Error::DuplicateAttempt);
        }
        let (transport_attempt, connections) = match self.preflight_transport(&workers, &planned) {
            Ok(value) => value,
            Err(error) => {
                self.stop();
                return Err(error);
            }
        };
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
        // The existing ledger bounds issued leases. Record transport history
        // only after atomic admission, never evict on settle or void.
        self.seen_attempt_labels.insert(attempt.to_owned());
        if let Some(id) = transport_attempt {
            self.seen_attempts.insert(id);
        }
        self.connections.extend(connections);
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
                    approval_requested: false,
                    planned: planned.get(&name).copied(),
                    transport: None,
                    acp_instance: None,
                    pending: [0; FRAME_LEN],
                    pending_len: 0,
                },
            );
            keys.push(WorkerKey {
                worker: name,
                identity,
            });
        }
        Ok(keys)
    }

    fn preflight_transport(
        &self,
        workers: &[Worker<'_>],
        planned: &BTreeMap<String, Identity>,
    ) -> Result<TransportHistory, Error> {
        let invalid = Error::Transport(synthetic_transport::Error::Identity);
        let mut attempt = None;
        let mut connections = BTreeSet::new();
        let mut worker_ids = BTreeSet::new();
        for worker in workers {
            let id = planned.get(&worker.demand.worker);
            let requires = matches!(worker.plan.protocol(), Protocol::AcpV1 | Protocol::AcpV2);
            if requires != id.is_some() {
                return Err(invalid);
            }
            if let Some(id) = id {
                if !id.valid()
                    || self.owner_scope != Some((id.project, id.session))
                    || attempt.is_some_and(|old| old != id.attempt)
                    || self.seen_attempts.contains(&id.attempt)
                    || self.connections.contains(&id.connection)
                    || !connections.insert(id.connection)
                    || !worker_ids.insert(id.worker)
                {
                    return Err(invalid);
                }
                attempt = Some(id.attempt);
            }
        }
        if connections.len() != planned.len() {
            return Err(invalid);
        }
        Ok((attempt, connections))
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
        if self.row(key)?.planned != Some(identity) {
            self.stop();
            return Err(Error::Transport(synthetic_transport::Error::Identity));
        }
        self.rows
            .get_mut(&key.worker)
            .ok_or(Error::Foreign)?
            .transport = Some(identity);
        Ok(())
    }

    /// Opt into typed ingress before any dispatch. The adapter must have
    /// completed its restricted handshake and match the exact reviewed plan.
    pub fn bind_acp_v1(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
        adapter: &Adapter,
        now: u64,
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.planned != Some(source) || !adapter.source_matches(source) {
            return Err(Error::Foreign);
        }
        if row.plan.protocol() != Protocol::AcpV1
            || !adapter.plan_matches(&row.plan)
            || adapter.phase() != acp_v1::Phase::Ready
        {
            self.stop();
            return Err(Error::Phase);
        }
        if let Err(error) = adapter.ready_for(current) {
            self.stop();
            return Err(Error::Acp(error));
        }
        self.bind_transport(key, current, source, now)?;
        self.rows
            .get_mut(&key.worker)
            .ok_or(Error::Foreign)?
            .acp_instance = Some(adapter.instance());
        Ok(())
    }

    /// Bound adapters cannot bypass the fixed envelope with raw events.
    pub fn record_frame(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        source: Identity,
        bytes: &[u8],
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.transport != Some(source) {
            return Err(Error::Foreign);
        }
        if row.acp_instance.is_some() {
            self.stop();
            return Err(Error::Phase);
        }
        if row.pending_len != 0 {
            self.stop();
            return Err(Error::Transport(synthetic_transport::Error::Partial));
        }
        self.accept_frame(key, current, now, bytes)
    }

    /// Bounded internal stream adapter. One partial frame per existing row;
    /// no new event tracker or ledger. The caller owns the actual byte stream.
    pub fn record_chunk(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        source: Identity,
        mut bytes: &[u8],
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if !row.dispatched || row.transport.is_none() {
            return Err(Error::Phase);
        }
        // The trusted reader must identify its actual connection before
        // touching the buffer, owner clock or shared policy.
        if row.transport != Some(source) {
            return Err(Error::Foreign);
        }
        if row.acp_instance.is_some() {
            self.stop();
            return Err(Error::Phase);
        }
        self.check(key, current)?;
        self.tick(now, current.policy)?;
        if bytes.len() > MAX_CHUNK {
            self.stop();
            return Err(Error::Transport(synthetic_transport::Error::ChunkLimit));
        }
        while !bytes.is_empty() {
            if self.stopped()
                || !matches!(
                    self.row(key)?.track.phase(),
                    Phase::AwaitingStart | Phase::Running
                )
            {
                self.stop();
                return Err(Error::Phase);
            }
            let row = self.rows.get_mut(&key.worker).ok_or(Error::Foreign)?;
            let count = (FRAME_LEN - row.pending_len).min(bytes.len());
            row.pending[row.pending_len..row.pending_len + count].copy_from_slice(&bytes[..count]);
            row.pending_len += count;
            bytes = &bytes[count..];
            if row.pending_len == FRAME_LEN {
                let frame = row.pending;
                row.pending_len = 0;
                self.accept_frame(key, current, now, &frame)?;
            }
        }
        Ok(())
    }

    fn accept_frame(
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
        let row = self.row(key)?;
        if row.transport != Some(identity) {
            return Err(Error::Foreign);
        }
        let partial = row.pending_len != 0;
        self.stop();
        if partial {
            return Err(Error::Transport(synthetic_transport::Error::Partial));
        }
        Ok(())
    }

    /// Exact trusted owner cancellation, not a protocol acknowledgement.
    pub fn cancel_transport(&mut self, key: &WorkerKey, identity: Identity) -> Result<(), Error> {
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
        if self.row(key)?.acp_instance.is_some() {
            return Err(Error::Phase);
        }
        self.dispatch_exact(key, current, now)
    }

    /// Typed rows cannot enter the ordinary raw-fixture dispatch route.
    /// Require the same ready adapter and current policy immediately before
    /// the existing Session issues its synthetic dispatch transition.
    pub fn dispatch_acp_v1(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
        adapter: &Adapter,
        now: u64,
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.transport != Some(source) || !adapter.source_matches(source) {
            return Err(Error::Foreign);
        }
        if !row
            .acp_instance
            .as_ref()
            .is_some_and(|instance| Arc::ptr_eq(instance, &adapter.instance()))
            || !adapter.plan_matches(&row.plan)
        {
            self.stop();
            return Err(Error::Phase);
        }
        if let Err(error) = adapter.ready_for(current) {
            self.stop();
            return Err(Error::Acp(error));
        }
        self.dispatch_exact(key, current, now)
    }

    fn dispatch_exact(
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
        if event == Event::ApprovalNeeded {
            row.approval_requested = true;
        }
        // No approval/resume authority yet. Approval requests and failures
        // stop the wave rather than silently allowing the requested effect.
        if matches!(event, Event::ApprovalNeeded | Event::ReportedFailure) {
            self.stop();
        }
        Ok(())
    }

    /// Trusted typed ACP v1 prompt ingress over the existing wave ledger.
    /// Fixture raw frame APIs remain available for synthetic testing only.
    /// Every local adapter fault stops the wave, without claiming OS drain.
    pub fn record_acp_v1(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        adapter: &mut Adapter,
        ingress: Ingress<'_>,
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.transport != Some(ingress.source) || !adapter.source_matches(ingress.source) {
            return Err(Error::Foreign);
        }
        if !row.dispatched
            || row.plan.protocol() != Protocol::AcpV1
            || !adapter.plan_matches(&row.plan)
            || !row
                .acp_instance
                .as_ref()
                .is_some_and(|instance| Arc::ptr_eq(instance, &adapter.instance()))
        {
            adapter.close();
            self.stop();
            return Err(Error::Phase);
        }
        if let Err(error) = self
            .check(key, current)
            .and_then(|()| self.tick(now, current.policy))
        {
            adapter.close();
            self.stop();
            return Err(error);
        }
        let frame = match adapter.accept(ingress.source, current, ingress.observation) {
            Ok(frame) => frame,
            Err(error) => {
                adapter.close();
                self.stop();
                return Err(Error::Acp(error));
            }
        };
        let bytes = match frame.encode() {
            Ok(bytes) => bytes,
            Err(error) => {
                adapter.close();
                self.stop();
                return Err(Error::Transport(error));
            }
        };
        if let Err(error) = self.accept_frame(key, current, now, &bytes) {
            adapter.close();
            self.stop();
            return Err(error);
        }
        Ok(())
    }

    fn exact_acp_cancellation(
        &self,
        key: &WorkerKey,
        source: Identity,
        adapter: &Adapter,
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.transport != Some(source)
            || !adapter.source_matches(source)
            || !row
                .acp_instance
                .as_ref()
                .is_some_and(|instance| Arc::ptr_eq(instance, &adapter.instance()))
        {
            return Err(Error::Foreign);
        }
        if !row.dispatched || !adapter.plan_matches(&row.plan) {
            return Err(Error::Phase);
        }
        Ok(())
    }

    /// Stop the shared wave and emit at most one cancellation plan.
    /// This does not release resources, send bytes or prove process exit.
    pub fn cancel_acp_v1(
        &mut self,
        key: &WorkerKey,
        source: Identity,
        adapter: &mut Adapter,
    ) -> Result<CancelPlan, Error> {
        self.exact_acp_cancellation(key, source, adapter)?;
        self.stop();
        adapter.cancellation().map_err(Error::Acp)
    }

    /// Additional distinct permission requests while cancellation is pending
    /// can only produce a cancelled response, never an affirmative selection.
    pub fn cancel_pending_acp_permission(
        &mut self,
        key: &WorkerKey,
        source: Identity,
        adapter: &mut Adapter,
        session_id: &str,
        rpc_id: u64,
    ) -> Result<CancelPlan, Error> {
        self.exact_acp_cancellation(key, source, adapter)?;
        if !self.stopped() {
            return Err(Error::Phase);
        }
        adapter
            .pending_cancellation(session_id, rpc_id)
            .map_err(Error::Acp)
    }

    /// A stopped wave may record a denied approval for its exact live row.
    /// This is not authorization and does not resume a stopped wave.
    pub fn check_denied_review(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
    ) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.transport != Some(source) {
            return Err(Error::Foreign);
        }
        if !row.dispatched || !row.approval_requested || !self.stopped() {
            return Err(Error::Phase);
        }
        self.check(key, current)
    }

    /// Trusted terminal observation for a nontransport worker.
    pub fn settle(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        measured: Option<(u64, u64)>,
    ) -> Result<ResultState, Error> {
        if self.row(key)?.transport.is_some() {
            return Err(Error::Phase);
        }
        self.settle_exact(key, current, now, measured)
    }

    /// Trusted terminal observer must identify the bound connection. This
    /// observation is not a protocol event or actual process-exit proof.
    pub fn settle_transport(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        source: Identity,
        measured: Option<(u64, u64)>,
    ) -> Result<ResultState, Error> {
        if self.row(key)?.transport != Some(source) {
            return Err(Error::Foreign);
        }
        self.settle_exact(key, current, now, measured)
    }

    /// MeasuredReport is diagnostic, not publication permission.
    fn settle_exact(
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
        if self.row(key)?.track.phase() != Phase::Draining || self.row(key)?.pending_len != 0 {
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

    /// Trusted never-started evidence for a nontransport worker.
    pub fn void_unstarted(&mut self, key: &WorkerKey) -> Result<(), Error> {
        if self.row(key)?.planned.is_some() {
            return Err(Error::Phase);
        }
        self.void_exact(key)
    }

    /// Trusted never-started evidence must match the predeclared identity,
    /// including before connection binding. This is not actual OS evidence.
    pub fn void_transport_unstarted(
        &mut self,
        key: &WorkerKey,
        source: Identity,
    ) -> Result<(), Error> {
        if self.row(key)?.planned != Some(source) {
            return Err(Error::Foreign);
        }
        self.void_exact(key)
    }

    fn void_exact(&mut self, key: &WorkerKey) -> Result<(), Error> {
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
