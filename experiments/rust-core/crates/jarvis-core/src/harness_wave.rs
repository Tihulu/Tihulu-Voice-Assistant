// SPDX-License-Identifier: AGPL-3.0-only
//! One shared synthetic Session owns the harness wave. No launch, transport,
//! process observation or permission issuance. All observations are trusted.
use crate::budget::{Amount, Demand, Snapshot};
use crate::harness::{self, Capability, Event, EventKey, EventTrack, Manifest, Negotiated, Phase};
use crate::session::{self, Handle, Session, Terminal};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

pub struct Worker {
    pub demand: Demand,
    pub plan: Negotiated,
}

pub struct Current<'a> {
    pub manifest: &'a Manifest,
    pub allowed: &'a BTreeSet<Capability>,
    pub local_only: bool,
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
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Harness(harness::Error),
    Session(session::Error),
    Foreign,
    Phase,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResultState {
    MeasuredReport,
    NoPublication,
}

pub struct HarnessWave {
    session: Session,
    rows: BTreeMap<String, Row>,
}

impl HarnessWave {
    pub fn new(session: Session) -> Result<Self, Error> {
        if session.stopped() || session.snapshot().reserved != Amount::default() {
            return Err(Error::Phase);
        }
        Ok(Self {
            session,
            rows: BTreeMap::new(),
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
        workers: Vec<Worker>,
        now: u64,
    ) -> Result<Vec<WorkerKey>, Error> {
        if !self.rows.is_empty() {
            return Err(Error::Phase);
        }
        let demands: Vec<_> = workers.iter().map(|w| w.demand.clone()).collect();
        let handles = self
            .session
            .reserve_wave(attempt, &demands, now)
            .map_err(Error::Session)?;
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
        self.session.resize_memory(ram, vram).map_err(Error::Session)
    }

    fn check(&mut self, key: &WorkerKey, current: &Current<'_>) -> Result<(), Error> {
        let result = self.row(key)?.plan.recheck(
            current.manifest,
            current.allowed,
            current.local_only,
        );
        if let Err(error) = result {
            self.stop();
            return Err(Error::Harness(error));
        }
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
        let row = self.rows.get_mut(&key.worker).ok_or(Error::Foreign)?;
        if let Err(error) = self.session.dispatch(&row.handle, now) {
            self.stop();
            return Err(Error::Session(error));
        }
        row.dispatched = true;
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
        if terminal != Terminal::Measured || outcome != harness::Outcome::ReportedSuccessAndDrained {
            self.stop();
            return Ok(ResultState::NoPublication);
        }
        Ok(ResultState::MeasuredReport)
    }

    /// Trusted evidence that the prepared worker never started.
    pub fn void_unstarted(&mut self, key: &WorkerKey) -> Result<(), Error> {
        let row = self.row(key)?;
        if row.dispatched {
            return Err(Error::Phase);
        }
        self.session
            .void_unstarted(&row.handle)
            .map_err(Error::Session)?;
        self.rows.remove(&key.worker);
        Ok(())
    }
}
