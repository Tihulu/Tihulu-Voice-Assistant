// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_store_fixture::owned::{Admission, OwnedStore, Ticket};
use jarvis_store_fixture::{Error, State, Store};
use rusqlite::Connection;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const S: [u8; 16] = [2; 16];
const C: [u8; 16] = [4; 16];
const D: [u8; 16] = [5; 16];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-owned-turn-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn db(&self) -> Connection {
        Connection::open(self.0.join("fixture.sqlite")).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn setup(db: Connection) -> OwnedStore {
    let store = Store::initialize(db).unwrap();
    store.session(S, None).unwrap();
    OwnedStore::new(store)
}

fn ticket(admission: Admission) -> Ticket {
    match admission {
        Admission::New(_, ticket) => ticket,
        Admission::Replay(_) => panic!("expected a new admission"),
    }
}

#[test]
fn replay_never_creates_a_second_ticket() {
    let mut owner = setup(Connection::open_in_memory().unwrap());
    let first = ticket(owner.submit(S, C, 0, "one", "Hello").unwrap());
    assert!(matches!(
        owner.submit(S, C, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    assert_eq!(owner.complete_reaped(&first, "Done"), Ok(2));
    assert!(matches!(
        owner.submit(S, C, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    assert_eq!(owner.complete_reaped(&first, "Again"), Err(Error::Unknown));
    assert_eq!(owner.history(S).unwrap().1.len(), 2);
}

#[test]
fn foreign_ticket_cannot_complete_or_cancel() {
    let mut a = setup(Connection::open_in_memory().unwrap());
    let mut b = setup(Connection::open_in_memory().unwrap());
    let first = ticket(a.submit(S, C, 0, "one", "Hello").unwrap());
    let foreign = ticket(b.submit(S, C, 0, "one", "Hello").unwrap());
    assert_eq!(a.complete_reaped(&foreign, "wrong"), Err(Error::Conflict));
    assert_eq!(a.cancel(&foreign, 1), Err(Error::Conflict));
    assert_eq!(a.observe_drained(&foreign), Err(Error::Conflict));
    assert_eq!(a.complete_reaped(&first, "right"), Ok(2));
    assert_eq!(b.history(S).unwrap().0, 1);
}

#[test]
fn cancelled_ticket_holds_until_exact_drain() {
    let mut owner = setup(Connection::open_in_memory().unwrap());
    let first = ticket(owner.submit(S, C, 0, "one", "Hello").unwrap());
    assert_eq!(owner.observe_drained(&first), Err(Error::Conflict));
    assert_eq!(owner.cancel(&first, 1), Ok(2));
    assert_eq!(owner.complete_reaped(&first, "late"), Err(Error::Conflict));
    assert!(matches!(
        owner.submit(S, D, 2, "next", "New"),
        Err(Error::Busy)
    ));
    assert_eq!(owner.archive(S, 2), Err(Error::Busy));
    assert_eq!(owner.observe_drained(&first), Ok(3));
    let second = ticket(owner.submit(S, D, 3, "next", "New").unwrap());
    assert_eq!(owner.complete_reaped(&first, "late"), Err(Error::Conflict));
    assert_eq!(owner.complete_reaped(&second, "Done"), Ok(5));
}

#[test]
fn restart_never_recreates_publication_authority() {
    let f = Fixture::new();
    let mut owner = setup(f.db());
    let old = ticket(owner.submit(S, C, 0, "one", "Hello").unwrap());
    drop(owner);
    let mut reopened = OwnedStore::new(Store::reopen(f.db()).unwrap());
    assert!(matches!(
        reopened.submit(S, C, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    assert_eq!(reopened.complete_reaped(&old, "late"), Err(Error::Unknown));
    assert_eq!(reopened.recover_cancel(S, 0).unwrap_err(), Error::Conflict);
    let recovery = reopened.recover_cancel(S, 1).unwrap();
    assert_eq!(
        reopened.recovery(S).unwrap().unwrap().state,
        State::Cancelled
    );
    assert_eq!(
        reopened.complete_reaped(&recovery, "late"),
        Err(Error::Conflict)
    );
    assert_eq!(reopened.observe_drained(&old), Err(Error::Conflict));
    assert_eq!(reopened.observe_drained(&recovery), Ok(3));
    assert_eq!(reopened.history(S).unwrap().1.len(), 1);
}

#[test]
fn second_connection_replay_is_not_a_runtime_ticket() {
    let f = Fixture::new();
    let mut owner = setup(f.db());
    let first = ticket(owner.submit(S, C, 0, "one", "Hello").unwrap());
    let mut other = OwnedStore::new(Store::reopen(f.db()).unwrap());
    assert!(matches!(
        other.submit(S, C, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    assert_eq!(other.complete_reaped(&first, "wrong"), Err(Error::Unknown));
    assert_eq!(owner.complete_reaped(&first, "right"), Ok(2));
    assert_eq!(other.history(S).unwrap().1.len(), 2);
}

#[test]
fn failed_completion_preserves_ticket() {
    let mut owner = setup(Connection::open_in_memory().unwrap());
    let first = ticket(owner.submit(S, C, 0, "one", "Hello").unwrap());
    assert_eq!(owner.complete_reaped(&first, ""), Err(Error::Conflict));
    assert_eq!(owner.complete_reaped(&first, "Done"), Ok(2));
}

#[test]
fn cancelled_recovery_can_be_reopened_without_second_revision() {
    let f = Fixture::new();
    let mut first = setup(f.db());
    let ticket = ticket(first.submit(S, C, 0, "one", "Hello").unwrap());
    first.cancel(&ticket, 1).unwrap();
    drop(first);
    let mut owner = OwnedStore::new(Store::reopen(f.db()).unwrap());
    assert_eq!(owner.recover_cancel(S, 1).unwrap_err(), Error::Conflict);
    let recovery = owner.recover_cancel(S, 2).unwrap();
    assert_eq!(owner.history(S).unwrap().0, 2);
    assert_eq!(owner.recover_cancel(S, 2).unwrap_err(), Error::Busy);
    assert_eq!(
        owner.complete_reaped(&recovery, "late"),
        Err(Error::Conflict)
    );
    assert_eq!(owner.observe_drained(&recovery), Ok(3));
    assert_eq!(owner.recover_cancel(S, 3).unwrap_err(), Error::Unknown);
}

#[test]
fn recovery_does_not_cancel_newer_turn_at_stale_revision() {
    let f = Fixture::new();
    let mut first = setup(f.db());
    let one = ticket(first.submit(S, C, 0, "one", "Hello").unwrap());
    first.complete_reaped(&one, "Done").unwrap();
    first.submit(S, D, 2, "two", "Next").unwrap();
    drop(first);
    let mut owner = OwnedStore::new(Store::reopen(f.db()).unwrap());
    assert_eq!(owner.recover_cancel(S, 1).unwrap_err(), Error::Conflict);
    let pending = owner.recovery(S).unwrap().unwrap();
    assert_eq!(pending.key, "two");
    assert_eq!(pending.state, State::Pending);
    let recovery = owner.recover_cancel(S, 3).unwrap();
    assert_eq!(owner.observe_drained(&recovery), Ok(5));
}

#[test]
fn database_failure_does_not_leave_a_partial_turn_or_lose_ticket() {
    let f = Fixture::new();
    let mut owner = setup(f.db());
    let fault = f.db();
    fault
        .execute_batch(
            "CREATE TRIGGER fixture_abort BEFORE UPDATE OF revision ON sessions
             BEGIN SELECT RAISE(ABORT, 'fixture'); END;",
        )
        .unwrap();
    assert!(matches!(
        owner.submit(S, C, 0, "one", "Hello"),
        Err(Error::Database)
    ));
    assert_eq!(owner.history(S).unwrap(), (0, vec![]));
    assert_eq!(owner.recovery(S).unwrap(), None);
    fault.execute_batch("DROP TRIGGER fixture_abort;").unwrap();
    let first = ticket(owner.submit(S, C, 0, "one", "Hello").unwrap());
    fault
        .execute_batch(
            "CREATE TRIGGER fixture_abort BEFORE UPDATE OF revision ON sessions
             BEGIN SELECT RAISE(ABORT, 'fixture'); END;",
        )
        .unwrap();
    assert_eq!(
        owner.complete_reaped(&first, "Done"),
        Err(Error::Database)
    );
    assert_eq!(owner.history(S).unwrap().0, 1);
    assert_eq!(owner.history(S).unwrap().1.len(), 1);
    assert_eq!(owner.recovery(S).unwrap().unwrap().state, State::Pending);
    fault.execute_batch("DROP TRIGGER fixture_abort;").unwrap();
    assert_eq!(owner.complete_reaped(&first, "Done"), Ok(2));
    assert_eq!(owner.history(S).unwrap().1.len(), 2);
    drop(owner);
    assert!(Store::reopen(f.db()).is_ok());
}

#[test]
fn admission_limit_does_not_commit_untracked_durable_work() {
    let db = Connection::open_in_memory().unwrap();
    let store = Store::initialize(db).unwrap();
    for i in 0..65u8 {
        store.session([i; 16], None).unwrap();
    }
    let mut owner = OwnedStore::new(store);
    let mut tickets = Vec::new();
    for i in 0..64u8 {
        tickets.push(ticket(
            owner.submit([i; 16], C, 0, "one", "Hello").unwrap(),
        ));
    }
    assert!(matches!(
        owner.submit([64; 16], C, 0, "one", "Hello"),
        Err(Error::Limit)
    ));
    assert_eq!(owner.history([64; 16]).unwrap(), (0, vec![]));
    assert!(matches!(
        owner.submit([0; 16], C, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    owner.complete_reaped(&tickets[0], "Done").unwrap();
    assert!(matches!(
        owner.submit([64; 16], C, 0, "one", "Hello"),
        Ok(Admission::New(_, _))
    ));
}
