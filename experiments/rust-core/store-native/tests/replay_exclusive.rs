// SPDX-License-Identifier: AGPL-3.0-only
#![cfg(target_os = "linux")]
use jarvis_core::budget::{Amount, Demand};
use jarvis_core::harness::{self, Capability, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::{Current, PolicyStamp, Worker};
use jarvis_core::session::Session;
use jarvis_core::synthetic_transport::Identity;
use jarvis_store_fixture::admission::{project_scope, session_scope};
use jarvis_store_fixture::exclusive::ExclusiveStore;
use jarvis_store_fixture::replay::exclusive::ExclusiveReplay;
use jarvis_store_fixture::Error;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::os::unix::fs::{symlink, DirBuilderExt};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const P: [u8; 16] = [1; 16];
const S: [u8; 16] = [2; 16];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-exclusive-replay-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::DirBuilder::new().mode(0o700).create(&path).unwrap();
        Self(path)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn amount() -> Amount {
    Amount {
        ram: 16,
        vram: 8,
        generated: 16,
        prompt: 112,
    }
}

#[test]
fn v1_and_v3_cooperating_owners_share_one_lock() {
    let f = Fixture::new();
    let owner = ExclusiveReplay::initialize(&f.0, P, S).unwrap();
    assert!(matches!(ExclusiveReplay::reopen(&f.0), Err(Error::Busy)));
    assert!(matches!(
        ExclusiveStore::open(&f.0, false),
        Err(Error::Busy)
    ));
    drop(owner);
    assert!(matches!(
        ExclusiveStore::open(&f.0, false),
        Err(Error::Schema)
    ));
    assert!(ExclusiveReplay::reopen(&f.0).is_ok());
}

#[test]
fn lock_moves_to_wave_and_drop_retains_recovery() {
    let f = Fixture::new();
    let owner = ExclusiveReplay::initialize(&f.0, P, S).unwrap();
    let session = Session::new(amount(), 100, 100, 20).unwrap();
    let mut wave = owner.into_wave(session, P, S).unwrap();
    assert!(matches!(ExclusiveReplay::reopen(&f.0), Err(Error::Busy)));
    let id = Identity {
        project: project_scope(P),
        session: session_scope(S),
        attempt: [3; 32],
        worker: [4; 32],
        connection: [5; 32],
    };
    let m = Manifest {
        kind: Kind::OpenCode,
        protocol: Protocol::AcpV1,
        pin: [1; 32],
        supported: BTreeSet::from([Capability::Chat, Capability::Cancel]),
        route: Route::Local,
    };
    let worker = Worker {
        demand: Demand {
            worker: "a".into(),
            amount: amount(),
        },
        plan: harness::negotiate(&m, &m, &m.supported, &m.supported, true).unwrap(),
        current: Current {
            manifest: &m,
            allowed: &m.supported,
            policy: PolicyStamp {
                generation: 1,
                local_only: true,
            },
        },
    };
    let plan = BTreeMap::from([("a".into(), id)]);
    wave.prepare("one", vec![worker], plan, 100).unwrap();
    drop(wave);
    let mut owner = ExclusiveReplay::reopen(&f.0).unwrap();
    assert_eq!(owner.unresolved(S).unwrap().unwrap().attempt, id.attempt);
    assert!(owner
        .observe_drained(S, id.attempt, &[(id.worker, [9; 32])])
        .is_err());
    owner
        .observe_drained(S, id.attempt, &[(id.worker, id.connection)])
        .unwrap();
    assert!(owner.unresolved(S).unwrap().is_none());
}

#[test]
fn v3_reuses_sidecar_and_database_alias_refusal() {
    let f = Fixture::new();
    drop(ExclusiveReplay::initialize(&f.0, P, S).unwrap());
    let target = f.0.join("target");
    fs::write(&target, b"unchanged").unwrap();
    let sidecar = f.0.join("fixture.sqlite-journal");
    symlink(&target, &sidecar).unwrap();
    assert!(matches!(ExclusiveReplay::reopen(&f.0), Err(Error::Invalid)));
    assert_eq!(fs::read(target).unwrap(), b"unchanged");
    fs::remove_file(sidecar).unwrap();
    let alias = f.0.join("alias");
    fs::hard_link(f.0.join("fixture.sqlite"), &alias).unwrap();
    assert!(matches!(ExclusiveReplay::reopen(&f.0), Err(Error::Invalid)));
    fs::remove_file(alias).unwrap();
    assert!(ExclusiveReplay::reopen(&f.0).is_ok());
}
