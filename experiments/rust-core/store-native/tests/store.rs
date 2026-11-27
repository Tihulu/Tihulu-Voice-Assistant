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
    for (key, text) in [("", "valid"), ("a/b", "valid"), ("key", "\t"), ("key", "x\0y")] {
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
    db.execute_batch("PRAGMA foreign_keys=OFF; DELETE FROM projects;").unwrap();
    assert!(matches!(Store::reopen(db), Err(Error::Schema)));
}
