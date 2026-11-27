// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::approval::Binding;
use jarvis_core::approval_broker::{Action, Request, Scope};
use jarvis_core::budget::{Amount, Demand};
use jarvis_core::harness::{self, Capability, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::{Current, PolicyStamp, ResultState, Worker};
use jarvis_core::session::Session;
use jarvis_core::synthetic_transport::{Identity, FRAME_LEN};
use jarvis_store_fixture::admission::{project_scope, session_scope, AdmissionError, GuardedWave};
use jarvis_store_fixture::replay::{guarded::GuardedReplayStore, ReplayStore};
use jarvis_store_fixture::Store;
use rusqlite::Connection;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const P: [u8; 16] = [1; 16];
const S: [u8; 16] = [2; 16];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-admission-fixture-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        let f = Self(path);
        let old = Store::initialize(f.db()).unwrap();
        old.project(P, "Fixture").unwrap();
        old.session(S, Some(P)).unwrap();
        drop(old);
        drop(ReplayStore::upgrade_fixture(f.db()).unwrap());
        drop(GuardedReplayStore::upgrade_fixture(f.db()).unwrap());
        f
    }

    fn db(&self) -> Connection {
        Connection::open(self.0.join("fixture.sqlite")).unwrap()
    }

    fn store(&self) -> GuardedReplayStore {
        GuardedReplayStore::reopen(self.db()).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn capacity() -> Amount {
    Amount {
        ram: 16,
        vram: 8,
        generated: 16,
        prompt: 112,
    }
}

fn session() -> Session {
    Session::new(capacity(), 100, 100, 20).unwrap()
}

fn manifest() -> Manifest {
    Manifest {
        kind: Kind::OpenCode,
        protocol: Protocol::AcpV1,
        pin: [1; 32],
        supported: BTreeSet::from([Capability::Chat, Capability::Cancel]),
        route: Route::Local,
    }
}

fn current(m: &Manifest) -> Current<'_> {
    Current {
        manifest: m,
        allowed: &m.supported,
        policy: PolicyStamp {
            generation: 1,
            local_only: true,
        },
    }
}

fn worker(m: &Manifest) -> Worker<'_> {
    Worker {
        demand: Demand {
            worker: "a".into(),
            amount: capacity(),
        },
        plan: harness::negotiate(m, m, &m.supported, &m.supported, true).unwrap(),
        current: current(m),
    }
}

fn identity() -> Identity {
    Identity {
        project: project_scope(P),
        session: session_scope(S),
        attempt: [3; 32],
        worker: [4; 32],
        connection: [5; 32],
    }
}

fn planned(id: Identity) -> BTreeMap<String, Identity> {
    BTreeMap::from([("a".into(), id)])
}

fn frame(id: Identity, seq: u64, event: u8) -> [u8; FRAME_LEN] {
    let mut b = [0; FRAME_LEN];
    b[..4].copy_from_slice(b"JHF1");
    b[4] = 1;
    b[5] = event;
    b[6..14].copy_from_slice(&seq.to_be_bytes());
    b[14..46].copy_from_slice(&id.project);
    b[46..78].copy_from_slice(&id.session);
    b[78..110].copy_from_slice(&id.attempt);
    b[110..142].copy_from_slice(&id.worker);
    b[142..174].copy_from_slice(&id.connection);
    b
}

#[test]
fn admission_is_durable_before_dispatch_and_finish_requires_terminal_observation() {
    let f = Fixture::new();
    let m = manifest();
    let id = identity();
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    let keys = wave
        .prepare("one", vec![worker(&m)], planned(id), 100)
        .unwrap();
    assert_eq!(
        f.store().unresolved(S).unwrap().unwrap().attempt,
        id.attempt
    );
    assert_eq!(wave.finish(), Err(AdmissionError::Busy));
    wave.bind(&keys[0], &current(&m), id, 100).unwrap();
    wave.dispatch(&keys[0], &current(&m), 100).unwrap();
    wave.record_chunk(&keys[0], &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    wave.record_chunk(&keys[0], &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    assert_eq!(wave.finish(), Err(AdmissionError::Busy));
    assert_eq!(
        wave.settle(&keys[0], &current(&m), 103, id, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
    assert!(f.store().unresolved(S).unwrap().is_some());
    wave.finish().unwrap();
    assert!(f.store().unresolved(S).unwrap().is_none());
}

#[test]
fn competing_durable_claim_denies_keys_and_releases_only_prepared_reservation() {
    let f = Fixture::new();
    let m = manifest();
    let id = identity();
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    let mut competitor = f.store();
    competitor
        .claim_wave(P, S, "other", [8; 32], &[([9; 32], [10; 32])])
        .unwrap();
    assert!(matches!(
        wave.prepare("one", vec![worker(&m)], planned(id), 100),
        Err(AdmissionError::Storage(_))
    ));
    assert!(wave.stopped());
    assert_eq!(wave.snapshot().reserved, Amount::default());
    assert_eq!(competitor.unresolved(S).unwrap().unwrap().attempt, [8; 32]);
}

#[test]
fn dropping_owner_never_clears_durable_barrier() {
    let f = Fixture::new();
    let m = manifest();
    let id = identity();
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    wave.prepare("one", vec![worker(&m)], planned(id), 100)
        .unwrap();
    drop(wave);
    assert!(matches!(
        GuardedWave::new(f.store(), session(), P, S),
        Err(AdmissionError::Busy)
    ));
    assert!(f.store().unresolved(S).unwrap().is_some());
}

#[test]
fn failed_budget_preflight_never_burns_durable_identity() {
    let f = Fixture::new();
    let m = manifest();
    let id = identity();
    let mut small = capacity();
    small.ram = 1;
    let session = Session::new(small, 100, 100, 20).unwrap();
    let mut wave = GuardedWave::new(f.store(), session, P, S).unwrap();
    assert!(matches!(
        wave.prepare("one", vec![worker(&m)], planned(id), 100),
        Err(AdmissionError::Wave(_))
    ));
    assert!(f.store().unresolved(S).unwrap().is_none());
}

#[test]
fn stopped_prepared_wave_needs_exact_void_before_finish() {
    let f = Fixture::new();
    let m = manifest();
    let id = identity();
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    let keys = wave
        .prepare("one", vec![worker(&m)], planned(id), 100)
        .unwrap();
    wave.stop();
    let wrong = Identity {
        connection: [9; 32],
        ..id
    };
    assert!(wave.void_unstarted(&keys[0], wrong).is_err());
    assert_eq!(wave.finish(), Err(AdmissionError::Busy));
    wave.void_unstarted(&keys[0], id).unwrap();
    wave.finish().unwrap();
    assert!(f.store().unresolved(S).unwrap().is_none());
}

fn review_manifest() -> Manifest {
    let mut m = manifest();
    m.supported.insert(Capability::Propose);
    m
}

fn review_request(m: &Manifest, id: Identity) -> Request {
    Request {
        scope: Scope {
            project: id.project,
            session: id.session,
            turn: [6; 32],
            attempt: id.attempt,
            worker: id.worker,
        },
        action: Action::Plan {
            target: [7; 32],
            input: [8; 32],
        },
        binding: Binding {
            subject: [9; 32],
            action: [10; 32],
            policy: current(m).policy,
            expires_at: 120,
        },
        manifest: m.clone(),
    }
}

#[test]
fn exact_approval_event_records_denial_without_resume() {
    let f = Fixture::new();
    let m = review_manifest();
    let id = identity();
    let r = review_request(&m, id);
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    let keys = wave
        .prepare_for_turn("one", [6; 32], vec![worker(&m)], planned(id), 100)
        .unwrap();
    wave.bind(&keys[0], &current(&m), id, 100).unwrap();
    wave.dispatch(&keys[0], &current(&m), 100).unwrap();
    assert!(wave
        .record_denied_approval(&keys[0], &current(&m), id, [11; 32], &r, 101)
        .is_err());
    wave.record_chunk(&keys[0], &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    wave.record_chunk(&keys[0], &current(&m), 102, id, &frame(id, 2, 3))
        .unwrap();
    let mut wrong = r.clone();
    wrong.scope.turn = [12; 32];
    assert!(wave
        .record_denied_approval(&keys[0], &current(&m), id, [11; 32], &wrong, 103)
        .is_err());
    wave.record_denied_approval(&keys[0], &current(&m), id, [11; 32], &r, 103)
        .unwrap();
    assert!(wave.stopped());
    assert!(wave
        .record_denied_approval(&keys[0], &current(&m), id, [12; 32], &r, 104)
        .is_err());
    assert!(wave.dispatch(&keys[0], &current(&m), 104).is_err());
    assert_eq!(wave.snapshot().reserved, capacity());
    drop(wave);
    let db = f.db();
    let count: i64 = db
        .query_row("SELECT count(*) FROM harness_approval_replay", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(count, 1);
}

#[test]
fn stopped_without_approval_event_cannot_record_denial() {
    let f = Fixture::new();
    let m = review_manifest();
    let id = identity();
    let r = review_request(&m, id);
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    let keys = wave
        .prepare_for_turn("one", [6; 32], vec![worker(&m)], planned(id), 100)
        .unwrap();
    wave.bind(&keys[0], &current(&m), id, 100).unwrap();
    wave.dispatch(&keys[0], &current(&m), 100).unwrap();
    wave.stop();
    assert!(wave
        .record_denied_approval(&keys[0], &current(&m), id, [11; 32], &r, 101)
        .is_err());
    assert_eq!(wave.snapshot().reserved, capacity());
}

#[test]
fn storage_denial_does_not_reopen_review_or_release_reservation() {
    let f = Fixture::new();
    let m = review_manifest();
    let id = identity();
    let r = review_request(&m, id);
    let mut wave = GuardedWave::new(f.store(), session(), P, S).unwrap();
    let keys = wave
        .prepare_for_turn("one", [6; 32], vec![worker(&m)], planned(id), 100)
        .unwrap();
    wave.bind(&keys[0], &current(&m), id, 100).unwrap();
    wave.dispatch(&keys[0], &current(&m), 100).unwrap();
    wave.record_chunk(&keys[0], &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    wave.record_chunk(&keys[0], &current(&m), 102, id, &frame(id, 2, 3))
        .unwrap();
    f.store()
        .burn_approval(S, id.attempt, id.worker, [11; 32], r.binding.action)
        .unwrap();
    assert!(matches!(
        wave.record_denied_approval(&keys[0], &current(&m), id, [11; 32], &r, 103),
        Err(AdmissionError::Storage(_))
    ));
    let mut changed = r.clone();
    changed.binding.action = [13; 32];
    assert!(wave
        .record_denied_approval(&keys[0], &current(&m), id, [12; 32], &changed, 104)
        .is_err());
    assert!(wave.stopped());
    assert_eq!(wave.snapshot().reserved, capacity());
}
