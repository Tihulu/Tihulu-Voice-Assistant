use jarvis_store_fixture::{Error, Receipt, State, Store};
use rusqlite::Connection;
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Barrier};
use std::thread;

const P: [u8; 16] = [1; 16];
const S: [u8; 16] = [2; 16];
const T: [u8; 16] = [3; 16];
const C: [u8; 16] = [4; 16];
const D: [u8; 16] = [5; 16];

fn setup(db: Connection) -> Store {
    let s = Store::initialize(db).unwrap();
    s.project(P, "Fixture").unwrap();
    s.session(S, Some(P)).unwrap();
    s.session(T, None).unwrap();
    s
}

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-store-fixture-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn db(&self) -> Connection {
        Connection::open(self.0.join("fixture.sqlite")).unwrap()
    }
}

static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn exact_receipt_survives_file_reopen() {
    let f = Fixture::new();
    {
        let mut s = setup(f.db());
        assert_eq!(
            s.submit(S, C, 0, "key", "Hello").unwrap(),
            Receipt {
                sequence: 1,
                state: State::Pending,
                replay: false,
            }
        );
        assert_eq!(s.complete(S, C, "key", "Done"), Ok(2));
    }
    let mut s = Store::reopen(f.db()).unwrap();
    assert_eq!(
        s.submit(S, C, 0, "key", "Hello").unwrap(),
        Receipt {
            sequence: 1,
            state: State::Completed,
            replay: true,
        }
    );
    assert_eq!(s.submit(S, C, 0, "key", "changed"), Err(Error::Conflict));
    let (revision, messages) = s.history(S).unwrap();
    assert_eq!(revision, 2);
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[1].body, "Done");
}

#[test]
fn pending_and_cancelled_turn_survive_reopen() {
    let f = Fixture::new();
    {
        let mut s = setup(f.db());
        s.submit(S, C, 0, "first", "Hello").unwrap();
        assert_eq!(s.cancel(S, C, "first", 1), Ok(2));
    }
    let mut s = Store::reopen(f.db()).unwrap();
    assert_eq!(s.submit(S, D, 2, "next", "new"), Err(Error::Busy));
    assert_eq!(s.complete(S, C, "first", "late"), Err(Error::Conflict));
    assert_eq!(s.drain(S, D, "first"), Err(Error::Unknown));
    assert_eq!(s.drain(S, C, "first"), Ok(3));
    assert_eq!(s.drain(S, C, "first"), Err(Error::Conflict));
    assert_eq!(s.submit(S, D, 3, "next", "new").unwrap().sequence, 2);
}

#[test]
fn two_connections_exactly_one_new_receipt() {
    let f = Fixture::new();
    drop(setup(f.db()));
    let barrier = Arc::new(Barrier::new(2));
    let mut workers = Vec::new();
    for _ in 0..2 {
        let db = f.db();
        let barrier = barrier.clone();
        workers.push(thread::spawn(move || {
            let mut s = Store::reopen(db).unwrap();
            barrier.wait();
            s.submit(S, C, 0, "same", "Hello").unwrap()
        }));
    }
    let results: Vec<_> = workers.into_iter().map(|w| w.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| !r.replay).count(), 1);
    assert_eq!(results.iter().filter(|r| r.replay).count(), 1);
    let s = Store::reopen(f.db()).unwrap();
    assert_eq!(s.history(S).unwrap().0, 1);
    assert_eq!(s.history(S).unwrap().1.len(), 1);
}

#[test]
fn exact_client_session_and_revision_boundaries() {
    let mut s = setup(Connection::open_in_memory().unwrap());
    s.submit(S, C, 0, "key", "Hello").unwrap();
    assert_eq!(s.submit(S, D, 0, "key", "Hello"), Err(Error::Conflict));
    assert_eq!(s.complete(S, D, "key", "wrong"), Err(Error::Unknown));
    assert_eq!(s.complete(T, C, "key", "wrong"), Err(Error::Unknown));
    assert_eq!(s.cancel(S, C, "key", 0), Err(Error::Conflict));
    assert_eq!(s.drain(S, C, "key"), Err(Error::Conflict));
    assert_eq!(s.submit(T, C, 0, "key", "other").unwrap().sequence, 1);
}

#[test]
fn invalid_inputs_do_not_mutate() {
    let mut s = setup(Connection::open_in_memory().unwrap());
    for (key, text) in [
        ("", "valid"),
        ("a/b", "valid"),
        ("key", "\t"),
        ("key", "x\0y"),
    ] {
        assert_eq!(s.submit(S, C, 0, key, text), Err(Error::Invalid));
    }
    assert_eq!(s.submit(S, C, -1, "key", "ok"), Err(Error::Invalid));
    assert_eq!(
        s.submit(S, C, 0, "key", &"x".repeat(16_385)),
        Err(Error::Invalid)
    );
    assert_eq!(s.history(S).unwrap(), (0, vec![]));
}

#[test]
fn existing_schema_and_unknown_version_are_refused() {
    let db = Connection::open_in_memory().unwrap();
    db.execute_batch("CREATE TABLE unrelated(x);").unwrap();
    assert!(matches!(Store::initialize(db), Err(Error::Schema)));
    let db = Connection::open_in_memory().unwrap();
    db.execute_batch("PRAGMA user_version=99;").unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
    let f = Fixture::new();
    drop(setup(f.db()));
    let db = f.db();
    db.execute_batch("DROP TRIGGER no_archive_live;").unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}

#[test]
fn foreign_key_damage_refused_on_reopen() {
    let f = Fixture::new();
    drop(setup(f.db()));
    let db = f.db();
    db.execute_batch("PRAGMA foreign_keys=OFF; DELETE FROM projects;")
        .unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}

#[test]
fn recovery_and_archive_keep_live_turn_barrier() {
    let f = Fixture::new();
    let mut s = setup(f.db());
    s.submit(S, C, 0, "first", "Hello").unwrap();
    drop(s);
    let mut s = Store::reopen(f.db()).unwrap();
    let recovery = s.recovery(S).unwrap().unwrap();
    assert_eq!(recovery.client, C);
    assert_eq!(recovery.key, "first");
    assert_eq!(recovery.state, State::Pending);
    assert_eq!(s.archive(S, 1), Err(Error::Busy));
    assert_eq!(s.cancel(S, C, "first", 1), Ok(2));
    assert_eq!(s.recovery(S).unwrap().unwrap().state, State::Cancelled);
    assert_eq!(s.archive(S, 2), Err(Error::Busy));
    assert_eq!(s.drain(S, C, "first"), Ok(3));
    assert_eq!(s.recovery(S).unwrap(), None);
    assert_eq!(s.archive(S, 2), Err(Error::Conflict));
    assert_eq!(s.archive(S, 3), Ok(4));
    assert_eq!(s.archive(S, 4), Ok(4));
    assert_eq!(s.submit(S, C, 4, "next", "New"), Err(Error::Archived));
    assert!(s.submit(S, C, 0, "first", "Hello").unwrap().replay);
    assert_eq!(s.history(S).unwrap().1.len(), 1);
    assert_eq!(s.history(T).unwrap(), (0, vec![]));
}

#[test]
fn history_and_revision_are_consistent_during_parallel_writes() {
    let f = Fixture::new();
    drop(setup(f.db()));
    let mut writer = Store::reopen(f.db()).unwrap();
    let reader = Store::reopen(f.db()).unwrap();
    let worker = thread::spawn(move || {
        for i in 0..32 {
            let key = format!("key-{i}");
            writer.submit(S, C, 2 * i, &key, "Hello").unwrap();
            writer.complete(S, C, &key, "Done").unwrap();
        }
    });
    for _ in 0..128 {
        let (rev, messages) = reader.history(S).unwrap();
        assert_eq!(rev as usize, messages.len());
    }
    worker.join().unwrap();
    let (rev, messages) = reader.history(S).unwrap();
    assert_eq!(rev, 64);
    assert_eq!(messages.len(), 64);
}

#[test]
fn schema_prefix_wildcard_does_not_hide_unexpected_tables() {
    let f = Fixture::new();
    drop(setup(f.db()));
    let db = f.db();
    db.execute_batch("CREATE TABLE sqliteXunexpected(x);")
        .unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}

#[test]
fn history_unknown_session_is_not_empty_authorized_history() {
    let s = setup(Connection::open_in_memory().unwrap());
    assert_eq!(s.history(D), Err(Error::Unknown));
    assert_eq!(s.recovery(D), Err(Error::Unknown));
    assert_eq!(s.history(T).unwrap(), (0, vec![]));
}

#[test]
fn reopen_rejects_orphan_message_even_with_valid_foreign_keys() {
    let f = Fixture::new();
    drop(setup(f.db()));
    let db = f.db();
    db.execute(
        "INSERT INTO messages VALUES (?1,1,'user','orphan')",
        [S.as_slice()],
    )
    .unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}

#[test]
fn reopen_rejects_revision_drift() {
    let f = Fixture::new();
    let mut store = setup(f.db());
    store.submit(S, C, 0, "first", "Hello").unwrap();
    drop(store);
    let db = f.db();
    db.execute("UPDATE sessions SET revision=99 WHERE id=?1", [S.as_slice()])
        .unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}

#[test]
fn reopen_rejects_gap_even_if_receipt_and_revision_match() {
    let f = Fixture::new();
    drop(setup(f.db()));
    let db = f.db();
    db.execute(
        "INSERT INTO messages VALUES (?1,3,'user','gap')",
        [S.as_slice()],
    )
    .unwrap();
    db.execute(
        "INSERT INTO turns(session_id,client_id,request_key,user_seq,state)
         VALUES (?1,?2,'gap',3,'pending')",
        rusqlite::params![S.as_slice(), C.as_slice()],
    )
    .unwrap();
    db.execute("UPDATE sessions SET revision=1 WHERE id=?1", [S.as_slice()])
        .unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}

#[test]
fn reopen_accepts_valid_cancel_drain_complete_and_archive_history() {
    let f = Fixture::new();
    let mut store = setup(f.db());
    store.submit(S, C, 0, "first", "Hello").unwrap();
    store.cancel(S, C, "first", 1).unwrap();
    store.drain(S, C, "first").unwrap();
    store.submit(S, C, 3, "next", "Again").unwrap();
    store.complete(S, C, "next", "Done").unwrap();
    store.archive(S, 5).unwrap();
    drop(store);
    let store = Store::reopen(f.db()).unwrap();
    let (revision, messages) = store.history(S).unwrap();
    assert_eq!(revision, 6);
    assert_eq!(messages.len(), 3);
    assert_eq!(store.recovery(S).unwrap(), None);
}
