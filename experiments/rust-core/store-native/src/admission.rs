// SPDX-License-Identifier: AGPL-3.0-only
//! Disposable composition of the existing wave ledger and durable barrier.
//! No OS launch, authenticated evidence, provider IO, or real permission grant.
use crate::replay::guarded::{GuardedReplayStore, Peer};
use crate::replay::Digest;
use crate::{Error, Id};
use jarvis_core::acp_v1::{Adapter, Ingress};
use jarvis_core::approval_broker::{self, Broker, Observation, Request};
use jarvis_core::budget::Snapshot;
use jarvis_core::harness_wave::{
    self, Current, HarnessWave, PolicyStamp, ResultState, Worker, WorkerKey,
};
use jarvis_core::session::Session;
use jarvis_core::synthetic_transport::Identity;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AdmissionError {
    Storage(Error),
    Wave(harness_wave::Error),
    Review(approval_broker::Error),
    Invalid,
    Busy,
}

struct Review {
    turn: Digest,
    broker: Broker,
    denied: BTreeSet<Digest>,
}

struct Active {
    attempt: Digest,
    peers: Vec<Peer>,
    review: Option<Review>,
}

pub struct GuardedWave {
    store: GuardedReplayStore,
    wave: HarnessWave,
    project: Id,
    session: Id,
    active: Option<Active>,
    // Drop the store and wave before releasing the cooperating owner lock.
    #[cfg(target_os = "linux")]
    _owner_lock: Option<std::fs::File>,
}

/// Explicit injective mapping from existing 16-byte DB namespaces to
/// synthetic 32-byte transport scope. Not a hash or authentication scheme.
fn expand(prefix: &[u8; 16], id: Id) -> Digest {
    let mut result = [0; 32];
    result[..16].copy_from_slice(prefix);
    result[16..].copy_from_slice(&id);
    result
}

pub fn project_scope(id: Id) -> Digest {
    expand(b"JARVIS-PROJECT-1", id)
}

pub fn session_scope(id: Id) -> Digest {
    expand(b"JARVIS-SESSION-1", id)
}

impl GuardedWave {
    pub fn new(
        store: GuardedReplayStore,
        session: Session,
        project: Id,
        session_id: Id,
    ) -> Result<Self, AdmissionError> {
        if project == [0; 16] || session_id == [0; 16] {
            return Err(AdmissionError::Invalid);
        }
        if store
            .unresolved(session_id)
            .map_err(AdmissionError::Storage)?
            .is_some()
        {
            return Err(AdmissionError::Busy);
        }
        let wave =
            HarnessWave::new_scoped(session, project_scope(project), session_scope(session_id))
                .map_err(AdmissionError::Wave)?;
        Ok(Self {
            store,
            wave,
            project,
            session: session_id,
            active: None,
            #[cfg(target_os = "linux")]
            _owner_lock: None,
        })
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn retain_lock(mut self, lock: std::fs::File) -> Self {
        self._owner_lock = Some(lock);
        self
    }

    pub fn snapshot(&self) -> Snapshot {
        self.wave.snapshot()
    }

    pub fn stopped(&self) -> bool {
        self.wave.stopped()
    }

    /// All workers in this fixture use preplanned transport identities.
    /// Reserve purely in memory, then commit durable admission before any
    /// dispatch key escapes. If commit fails, stop and void prepared rows.
    pub fn prepare(
        &mut self,
        label: &str,
        workers: Vec<Worker<'_>>,
        planned: BTreeMap<String, Identity>,
        now: u64,
    ) -> Result<Vec<WorkerKey>, AdmissionError> {
        if self.active.is_some() {
            return Err(AdmissionError::Busy);
        }
        if workers.is_empty() || workers.len() != planned.len() || workers.len() > 10 {
            return Err(AdmissionError::Invalid);
        }
        let identities = workers
            .iter()
            .map(|worker| {
                planned
                    .get(&worker.demand.worker)
                    .copied()
                    .ok_or(AdmissionError::Invalid)
            })
            .collect::<Result<Vec<_>, _>>()?;
        let attempt = identities[0].attempt;
        let peers: Vec<_> = identities
            .iter()
            .map(|id| (id.worker, id.connection))
            .collect();
        let keys = self
            .wave
            .reserve_with_transport(label, workers, planned, now)
            .map_err(AdmissionError::Wave)?;
        if let Err(error) =
            self.store
                .claim_wave(self.project, self.session, label, attempt, &peers)
        {
            self.wave.stop();
            for (key, identity) in keys.iter().zip(identities) {
                self.wave
                    .void_transport_unstarted(key, identity)
                    .map_err(AdmissionError::Wave)?;
            }
            return Err(AdmissionError::Storage(error));
        }
        self.active = Some(Active {
            attempt,
            peers,
            review: None,
        });
        Ok(keys)
    }

    /// The trusted owner explicitly predeclares the turn namespace.
    /// Existing plain prepare does not enable typed approval review.
    pub fn prepare_for_turn(
        &mut self,
        label: &str,
        turn: Digest,
        workers: Vec<Worker<'_>>,
        planned: BTreeMap<String, Identity>,
        now: u64,
    ) -> Result<Vec<WorkerKey>, AdmissionError> {
        if turn == [0; 32] {
            return Err(AdmissionError::Invalid);
        }
        let stamp = workers
            .first()
            .ok_or(AdmissionError::Invalid)?
            .current
            .policy;
        let broker = Broker::new(
            project_scope(self.project),
            session_scope(self.session),
            stamp,
            now,
        )
        .map_err(AdmissionError::Review)?;
        let keys = self.prepare(label, workers, planned, now)?;
        self.active.as_mut().ok_or(AdmissionError::Invalid)?.review = Some(Review {
            turn,
            broker,
            denied: BTreeSet::new(),
        });
        Ok(keys)
    }

    /// Only the exact approval-needed event's owner may record a denied
    /// typed request. Register/revoke via the existing Broker, then persist
    /// its denial history. Never issue an affirmative receipt or resume.
    pub fn record_denied_approval(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
        id: Digest,
        request: &Request,
        now: u64,
    ) -> Result<(), AdmissionError> {
        self.wave
            .check_denied_review(key, current, source)
            .map_err(AdmissionError::Wave)?;
        let active = self.active.as_mut().ok_or(AdmissionError::Invalid)?;
        let review = active.review.as_mut().ok_or(AdmissionError::Invalid)?;
        let scope = request.scope;
        if scope.project != source.project
            || scope.session != source.session
            || scope.attempt != active.attempt
            || scope.attempt != source.attempt
            || scope.worker != source.worker
            || scope.turn != review.turn
            || &request.manifest != current.manifest
            || review.denied.contains(&source.worker)
        {
            return Err(AdmissionError::Invalid);
        }
        let observed = Observation {
            request,
            allowed: current.allowed,
            policy: current.policy,
        };
        let key = review
            .broker
            .register(id, request, &observed, now)
            .map_err(AdmissionError::Review)?;
        review.broker.revoke(&key).map_err(AdmissionError::Review)?;
        // Even a storage failure cannot retry or resurrect this review.
        review.denied.insert(source.worker);
        self.store
            .burn_approval(
                self.session,
                source.attempt,
                source.worker,
                id,
                request.binding.action,
            )
            .map_err(AdmissionError::Storage)
    }

    pub fn bind(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
        now: u64,
    ) -> Result<(), AdmissionError> {
        self.wave
            .bind_transport(key, current, source, now)
            .map_err(AdmissionError::Wave)
    }

    /// Typed handshake must be ready before binding a durably admitted row.
    pub fn bind_acp_v1(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
        adapter: &Adapter,
        now: u64,
    ) -> Result<(), AdmissionError> {
        if self.active.is_none() {
            return Err(AdmissionError::Invalid);
        }
        self.wave
            .bind_acp_v1(key, current, source, adapter, now)
            .map_err(AdmissionError::Wave)
    }

    pub fn record_acp_v1(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        adapter: &mut Adapter,
        ingress: Ingress<'_>,
    ) -> Result<(), AdmissionError> {
        if self.active.is_none() {
            return Err(AdmissionError::Invalid);
        }
        self.wave
            .record_acp_v1(key, current, now, adapter, ingress)
            .map_err(AdmissionError::Wave)
    }

    pub fn dispatch(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
    ) -> Result<(), AdmissionError> {
        if self.active.is_none() {
            return Err(AdmissionError::Invalid);
        }
        self.wave
            .dispatch(key, current, now)
            .map_err(AdmissionError::Wave)
    }

    pub fn dispatch_acp_v1(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        source: Identity,
        adapter: &Adapter,
        now: u64,
    ) -> Result<(), AdmissionError> {
        if self.active.is_none() {
            return Err(AdmissionError::Invalid);
        }
        self.wave
            .dispatch_acp_v1(key, current, source, adapter, now)
            .map_err(AdmissionError::Wave)
    }

    pub fn record_chunk(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        source: Identity,
        bytes: &[u8],
    ) -> Result<(), AdmissionError> {
        self.wave
            .record_chunk(key, current, now, source, bytes)
            .map_err(AdmissionError::Wave)
    }

    pub fn tick(&mut self, now: u64, policy: PolicyStamp) -> Result<(), AdmissionError> {
        self.wave.tick(now, policy).map_err(AdmissionError::Wave)
    }

    pub fn resize_memory(&mut self, ram: u64, vram: u64) -> Result<(), AdmissionError> {
        self.wave
            .resize_memory(ram, vram)
            .map_err(AdmissionError::Wave)
    }

    pub fn stop(&mut self) {
        self.wave.stop();
    }

    pub fn disconnect(&mut self, key: &WorkerKey, source: Identity) -> Result<(), AdmissionError> {
        self.wave
            .disconnect_transport(key, source)
            .map_err(AdmissionError::Wave)
    }

    pub fn settle(
        &mut self,
        key: &WorkerKey,
        current: &Current<'_>,
        now: u64,
        source: Identity,
        measured: Option<(u64, u64)>,
    ) -> Result<ResultState, AdmissionError> {
        self.wave
            .settle_transport(key, current, now, source, measured)
            .map_err(AdmissionError::Wave)
    }

    pub fn void_unstarted(
        &mut self,
        key: &WorkerKey,
        source: Identity,
    ) -> Result<(), AdmissionError> {
        self.wave
            .void_transport_unstarted(key, source)
            .map_err(AdmissionError::Wave)
    }

    /// All exact trusted terminal observations must have reached the
    /// existing wave before clearing the durable barrier. Drop never clears.
    pub fn finish(&mut self) -> Result<(), AdmissionError> {
        let active = self.active.as_ref().ok_or(AdmissionError::Invalid)?;
        if !self.wave.idle() {
            return Err(AdmissionError::Busy);
        }
        if let Err(error) = self
            .store
            .observe_drained(self.session, active.attempt, &active.peers)
        {
            self.wave.stop();
            return Err(AdmissionError::Storage(error));
        }
        self.active = None;
        Ok(())
    }
}
