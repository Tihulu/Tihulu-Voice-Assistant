// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_store_fixture::replay::{guarded::GuardedReplayStore, ReplayStore};
use jarvis_store_fixture::{Error, Store};
use rusqlite::Connection;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const P: [u8; 16] = [1; 16];
const S: [u8; 16] = [2; 16];
const A: [u8; 32] = [1; 32];
const W: [u8; 32] = [2; 32];
const C: [u8; 32] = [3; 32];
const B: [u8; 32] = [4; 32];
const X: [u8; 32] = [5; 32];
const Y: [u8; 32] = [6; 32];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-guard-fixture-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        let fixture = Self(path);
        let old = Store::initialize(fixture.db()).unwrap();
        old.project(P, "Fixture").unwrap();
        old.session(S, Some(P)).unwrap();
        drop(old);
        drop(ReplayStore::upgrade_fixture(fixture.db()).unwrap());
        fixture
    }

    fn db(&self) -> Connection {
        Connection::open(self.0.join("fixture.sqlite")).unwrap()
    }

    fn guarded(&self) -> GuardedReplayStore {
        GuardedReplayStore::upgrade_fixture(self.db()).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn restart_retains_unresolved_barrier_even_with_fresh_ids() {
    let f = Fixture::new();
    let mut store = f.guarded();
    store.claim_wave(P, S, "one", A, &[(W, C)]).unwrap();
    drop(store);
    assert!(matches!(ReplayStore::reopen(f.db()), Err(Error::Schema)));
    let mut store = GuardedReplayStore::reopen(f.db()).unwrap();
    let pending = store.unresolved(S).unwrap().unwrap();
    assert_eq!(pending.attempt, A);
    assert_eq!(pending.peers, vec![(W, C)]);
    assert!(store.claim_wave(P, S, "two", B, &[(X, Y)]).is_err());
    store.observe_drained(S, A, &[(W, C)]).unwrap();
    assert!(store.unresolved(S).unwrap().is_none());
    store.claim_wave(P, S, "two", B, &[(X, Y)]).unwrap();
    assert_eq!(store.unresolved(S).unwrap().unwrap().attempt, B);
}

#[test]
fn incomplete_wrong_or_duplicate_peer_evidence_cannot_release_barrier() {
    let f = Fixture::new();
    let mut store = f.guarded();
    store.claim_wave(P, S, "one", A, &[(W, C), (X, Y)]).unwrap();
    assert!(store.observe_drained(S, A, &[(W, C)]).is_err());
    assert!(store.observe_drained(S, A, &[(W, Y), (X, C)]).is_err());
    assert!(store.observe_drained(S, A, &[(W, C), (W, C)]).is_err());
    assert!(store.observe_drained(S, B, &[(W, C), (X, Y)]).is_err());
    assert!(store.unresolved(S).unwrap().is_some());
    store.observe_drained(S, A, &[(X, Y), (W, C)]).unwrap();
    assert!(store.observe_drained(S, A, &[(W, C), (X, Y)]).is_err());
}

#[test]
fn failed_peer_insert_rolls_back_barrier_and_identity_history() {
    let f = Fixture::new();
    let mut store = f.guarded();
    assert!(store.claim_wave(P, S, "one", A, &[(W, C), (X, C)]).is_err());
    assert!(store.unresolved(S).unwrap().is_none());
    store.claim_wave(P, S, "one", A, &[(W, C)]).unwrap();
}

#[test]
fn older_v2_history_never_implicitly_becomes_drained() {
    let f = Fixture::new();
    let mut v2 = ReplayStore::reopen(f.db()).unwrap();
    v2.claim_wave(P, S, "one", A, &[(W, C)]).unwrap();
    drop(v2);
    assert!(matches!(
        GuardedReplayStore::upgrade_fixture(f.db()),
        Err(Error::Busy)
    ));
    assert!(ReplayStore::reopen(f.db()).is_ok());
}

#[test]
fn stale_approval_and_archive_while_unresolved_are_denied() {
    let f = Fixture::new();
    let mut store = f.guarded();
    store.claim_wave(P, S, "one", A, &[(W, C)]).unwrap();
    let db = f.db();
    assert!(db
        .execute("UPDATE sessions SET archived=1 WHERE id=?1", [S.as_slice()])
        .is_err());
    store.burn_approval(S, A, W, B, X).unwrap();
    store.observe_drained(S, A, &[(W, C)]).unwrap();
    assert!(store.burn_approval(S, A, W, X, Y).is_err());
    drop(store);
    let db = f.db();
    assert!(db.execute("DELETE FROM harness_recovery", []).is_err());
    assert!(db
        .execute("UPDATE harness_recovery SET state='unresolved'", [])
        .is_err());
}

#[test]
fn separate_connections_cannot_bypass_unresolved_barrier() {
    let f = Fixture::new();
    let mut first = f.guarded();
    let mut second = GuardedReplayStore::reopen(f.db()).unwrap();
    first.claim_wave(P, S, "one", A, &[(W, C)]).unwrap();
    assert!(second.claim_wave(P, S, "two", B, &[(X, Y)]).is_err());
    assert!(second.unresolved(S).unwrap().is_some());
}
