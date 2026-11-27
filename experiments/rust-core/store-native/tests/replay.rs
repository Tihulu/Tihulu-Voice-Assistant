// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_store_fixture::{replay::ReplayStore, Error, Store};
use rusqlite::{params, Connection};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const P: [u8; 16] = [1; 16];
const S: [u8; 16] = [2; 16];
const CLIENT: [u8; 16] = [3; 16];
const A: [u8; 32] = [1; 32];
const W: [u8; 32] = [2; 32];
const C: [u8; 32] = [3; 32];
const R: [u8; 32] = [4; 32];
const X: [u8; 32] = [5; 32];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-replay-fixture-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn db(&self) -> Connection {
        Connection::open(self.0.join("fixture.sqlite")).unwrap()
    }

    fn setup(&self) -> Store {
        let s = Store::initialize(self.db()).unwrap();
        s.project(P, "Fixture").unwrap();
        s.session(S, Some(P)).unwrap();
        s
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn native_v2_reopen_denies_replay_and_preserves_existing_turns() {
    let f = Fixture::new();
    let mut old = f.setup();
    old.submit(S, CLIENT, 0, "first", "Hello").unwrap();
    old.complete(S, CLIENT, "first", "Done").unwrap();
    drop(old);
    let mut store = ReplayStore::upgrade_fixture(f.db()).unwrap();
    store.claim_wave(P, S, "one", A, &[(W, C)]).unwrap();
    store.burn_approval(S, A, W, R, X).unwrap();
    drop(store);
    assert!(matches!(Store::reopen(f.db()), Err(Error::Schema)));
    let mut store = ReplayStore::reopen(f.db()).unwrap();
    assert!(store.claim_wave(P, S, "one", X, &[(W, R)]).is_err());
    assert!(store.claim_wave(P, S, "two", A, &[(W, R)]).is_err());
    assert!(store.claim_wave(P, S, "two", X, &[(W, C)]).is_err());
    assert!(store.burn_approval(S, A, W, R, A).is_err());
    assert!(store.burn_approval(S, A, W, A, X).is_err());
    let db = f.db();
    let count: i64 = db
        .query_row("SELECT count(*) FROM messages", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 2);
}

#[test]
fn migration_refuses_live_turn_without_changing_v1() {
    let f = Fixture::new();
    let mut old = f.setup();
    old.submit(S, CLIENT, 0, "first", "Hello").unwrap();
    drop(old);
    assert!(matches!(
        ReplayStore::upgrade_fixture(f.db()),
        Err(Error::Busy)
    ));
    let old = Store::reopen(f.db()).unwrap();
    assert!(old.recovery(S).unwrap().is_some());
}

#[test]
fn duplicate_peer_rolls_back_native_claim() {
    let f = Fixture::new();
    drop(f.setup());
    let mut store = ReplayStore::upgrade_fixture(f.db()).unwrap();
    assert!(store.claim_wave(P, S, "one", A, &[(W, C), (X, C)]).is_err());
    store.claim_wave(P, S, "one", A, &[(W, C), (X, R)]).unwrap();
    store.burn_approval(S, A, X, R, W).unwrap();
    assert!(store.burn_approval(S, A, A, X, W).is_err());
}

#[test]
fn reopen_refuses_incomplete_direct_sql_plan() {
    let f = Fixture::new();
    drop(f.setup());
    drop(ReplayStore::upgrade_fixture(f.db()).unwrap());
    let db = f.db();
    db.execute(
        "INSERT INTO harness_wave_replay VALUES (?1,?2,'one',?3,2)",
        params![S.as_slice(), P.as_slice(), A.as_slice()],
    )
    .unwrap();
    db.execute(
        "INSERT INTO harness_connection_replay VALUES (?1,?2,?3,?4)",
        params![S.as_slice(), A.as_slice(), W.as_slice(), C.as_slice()],
    )
    .unwrap();
    drop(db);
    assert!(matches!(ReplayStore::reopen(f.db()), Err(Error::Schema)));
}

#[test]
fn reopen_refuses_schema_damage_even_with_version_two() {
    let f = Fixture::new();
    drop(f.setup());
    drop(ReplayStore::upgrade_fixture(f.db()).unwrap());
    let db = f.db();
    db.execute_batch("DROP TRIGGER harness_wave_no_delete;")
        .unwrap();
    drop(db);
    assert!(matches!(ReplayStore::reopen(f.db()), Err(Error::Schema)));
}
