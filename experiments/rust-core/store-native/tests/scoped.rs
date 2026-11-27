// SPDX-License-Identifier: AGPL-3.0-only
#![cfg(target_os = "linux")]
use jarvis_store_fixture::exclusive::ExclusiveStore;
use jarvis_store_fixture::owned::Admission;
use jarvis_store_fixture::scoped::{Mode, ScopedStore};
use jarvis_store_fixture::Error;
use std::fs;
use std::os::unix::fs::DirBuilderExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const S: [u8; 16] = [2; 16];
const T: [u8; 16] = [3; 16];
const C: [u8; 16] = [4; 16];
const D: [u8; 16] = [5; 16];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-scoped-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::DirBuilder::new().mode(0o700).create(&path).unwrap();
        Self(path)
    }

    fn setup(&self) -> ScopedStore {
        let store = ExclusiveStore::open(&self.0, true).unwrap();
        store.session(S, None).unwrap();
        store.session(T, None).unwrap();
        ScopedStore::new(store)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn read_only_and_other_session_are_isolated() {
    let f = Fixture::new();
    let mut store = f.setup();
    let writer = store.grant(S, C, Mode::Chat).unwrap();
    let reader = store.grant(S, D, Mode::Read).unwrap();
    let other = store.grant(T, C, Mode::Chat).unwrap();
    let Admission::New(_, ticket) = store.submit(&writer, 0, "one", "Hello").unwrap() else {
        panic!("new turn expected");
    };
    assert_eq!(store.history(&reader).unwrap().1.len(), 1);
    assert_eq!(store.history(&other).unwrap(), (0, vec![]));
    assert!(matches!(
        store.submit(&reader, 1, "two", "no"),
        Err(Error::Conflict)
    ));
    store.complete_reaped(&ticket, "Done").unwrap();
    assert_eq!(store.history(&reader).unwrap().1.len(), 2);
}

#[test]
fn rotation_preserves_namespace_and_does_not_duplicate_turn() {
    let f = Fixture::new();
    let mut store = f.setup();
    let old = store.grant(S, C, Mode::Chat).unwrap();
    let Admission::New(_, ticket) = store.submit(&old, 0, "one", "Hello").unwrap() else {
        panic!("new turn expected");
    };
    let new = store.rotate(&old).unwrap();
    assert_eq!(store.history(&old), Err(Error::Conflict));
    assert!(matches!(
        store.submit(&new, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    store.complete_reaped(&ticket, "Done").unwrap();
    store.revoke(&new).unwrap();
    assert!(matches!(store.rotate(&new), Err(Error::Conflict)));
    let approved = store.grant(S, C, Mode::Chat).unwrap();
    assert!(matches!(
        store.submit(&approved, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    assert_eq!(store.history(&approved).unwrap().1.len(), 2);
}

#[test]
fn revoked_client_cannot_submit_and_runtime_retains_ownership() {
    let f = Fixture::new();
    let mut store = f.setup();
    let grant = store.grant(S, C, Mode::Chat).unwrap();
    let Admission::New(_, ticket) = store.submit(&grant, 0, "one", "Hello").unwrap() else {
        panic!("new turn expected");
    };
    store.revoke(&grant).unwrap();
    assert!(matches!(
        store.submit(&grant, 1, "two", "No"),
        Err(Error::Conflict)
    ));
    store.cancel(&ticket, 1).unwrap();
    store.observe_drained(&ticket).unwrap();
    let new = store.grant(S, C, Mode::Read).unwrap();
    assert_eq!(store.history(&new).unwrap().0, 3);
}

#[test]
fn foreign_capability_does_not_resolve_even_with_same_ids() {
    let a = Fixture::new();
    let b = Fixture::new();
    let mut first = a.setup();
    let mut second = b.setup();
    let foreign = first.grant(S, C, Mode::Chat).unwrap();
    let local = second.grant(S, C, Mode::Read).unwrap();
    assert_eq!(second.history(&foreign), Err(Error::Conflict));
    assert!(matches!(
        second.submit(&foreign, 0, "one", "No"),
        Err(Error::Conflict)
    ));
    assert_eq!(second.history(&local).unwrap(), (0, vec![]));
}
