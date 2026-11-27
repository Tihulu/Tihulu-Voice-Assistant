// SPDX-License-Identifier: AGPL-3.0-only
#![cfg(target_os = "linux")]
use jarvis_store_fixture::exclusive::ExclusiveStore;
use jarvis_store_fixture::owned::Admission;
use jarvis_store_fixture::{Error, State};
use std::fs;
use std::os::unix::fs::{symlink, DirBuilderExt, PermissionsExt};
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

const S: [u8; 16] = [2; 16];
const C: [u8; 16] = [4; 16];
static NEXT: AtomicU64 = AtomicU64::new(1);

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "jarvis-exclusive-{}-{}",
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

#[test]
fn competing_owner_cannot_open_and_restart_preserves_hold() {
    let f = Fixture::new();
    let mut owner = ExclusiveStore::open(&f.0, true).unwrap();
    owner.session(S, None).unwrap();
    let Admission::New(_, old) = owner.submit(S, C, 0, "one", "Hello").unwrap() else {
        panic!("new turn required");
    };
    assert!(matches!(
        ExclusiveStore::open(&f.0, false),
        Err(Error::Busy)
    ));
    assert!(matches!(ExclusiveStore::open(&f.0, true), Err(Error::Busy)));
    drop(owner);
    let mut owner = ExclusiveStore::open(&f.0, false).unwrap();
    assert_eq!(owner.recovery(S).unwrap().unwrap().state, State::Pending);
    assert_eq!(owner.complete_reaped(&old, "late"), Err(Error::Unknown));
    let recovery = owner.recover_cancel(S, 1).unwrap();
    assert_eq!(owner.observe_drained(&recovery), Ok(3));
}

#[test]
#[ignore = "invoked by the parent process fixture"]
fn child_lock_probe() {
    let path = std::env::var_os("JARVIS_FIXTURE_LOCK_PROBE").expect("fixture path required");
    assert!(matches!(
        ExclusiveStore::open(&PathBuf::from(path), false),
        Err(Error::Busy)
    ));
}

#[test]
fn operating_system_lock_excludes_a_second_process() {
    let f = Fixture::new();
    let owner = ExclusiveStore::open(&f.0, true).unwrap();
    let status = Command::new(std::env::current_exe().unwrap())
        .args(["--ignored", "--exact", "child_lock_probe"])
        .env("JARVIS_FIXTURE_LOCK_PROBE", &f.0)
        .status()
        .unwrap();
    assert!(status.success());
    drop(owner);
    assert!(ExclusiveStore::open(&f.0, false).is_ok());
    assert!(f.0.join("owner.lock").is_file());
}

#[test]
fn rejects_nonprivate_directory_and_lock_aliases() {
    let f = Fixture::new();
    fs::set_permissions(&f.0, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(matches!(
        ExclusiveStore::open(&f.0, true),
        Err(Error::Invalid)
    ));
    fs::set_permissions(&f.0, fs::Permissions::from_mode(0o700)).unwrap();
    let target = f.0.join("target");
    fs::write(&target, b"unchanged").unwrap();
    symlink(&target, f.0.join("owner.lock")).unwrap();
    assert!(matches!(
        ExclusiveStore::open(&f.0, true),
        Err(Error::Invalid)
    ));
    assert_eq!(fs::read(target).unwrap(), b"unchanged");
}

#[test]
fn initialization_never_overwrites_existing_database() {
    let f = Fixture::new();
    let owner = ExclusiveStore::open(&f.0, true).unwrap();
    owner.session(S, None).unwrap();
    drop(owner);
    assert!(ExclusiveStore::open(&f.0, true).is_err());
    let owner = ExclusiveStore::open(&f.0, false).unwrap();
    assert_eq!(owner.history(S).unwrap(), (0, vec![]));
}

#[test]
fn refuses_symlink_sidecar_before_sqlite_open() {
    let f = Fixture::new();
    drop(ExclusiveStore::open(&f.0, true).unwrap());
    let target = f.0.join("target");
    fs::write(&target, b"unchanged").unwrap();
    symlink(&target, f.0.join("fixture.sqlite-journal")).unwrap();
    assert!(matches!(
        ExclusiveStore::open(&f.0, false),
        Err(Error::Invalid)
    ));
    assert_eq!(fs::read(target).unwrap(), b"unchanged");
}

#[test]
fn backup_preserves_history_without_overwriting_or_activating() {
    let f = Fixture::new();
    let mut owner = ExclusiveStore::open(&f.0, true).unwrap();
    owner.session(S, None).unwrap();
    let Admission::New(_, first) = owner.submit(S, C, 0, "one", "Hello").unwrap() else {
        panic!("expected new turn");
    };
    assert_eq!(owner.backup_idle(), Err(Error::Busy));
    assert!(!f.0.join("backup.sqlite").exists());
    assert!(!f.0.join("backup.staging").exists());
    owner.complete_reaped(&first, "Done").unwrap();
    owner.backup_idle().unwrap();
    assert_eq!(owner.backup_history(S).unwrap(), owner.history(S).unwrap());
    let Admission::New(_, second) = owner.submit(S, C, 2, "two", "Later").unwrap() else {
        panic!("expected new turn");
    };
    owner.complete_reaped(&second, "New").unwrap();
    assert_eq!(owner.backup_idle(), Err(Error::Conflict));
    assert_eq!(owner.backup_history(S).unwrap().0, 2);
    assert_eq!(owner.history(S).unwrap().0, 4);
}

#[test]
fn backup_refuses_unreconciled_durable_turn_after_restart() {
    let f = Fixture::new();
    let mut owner = ExclusiveStore::open(&f.0, true).unwrap();
    owner.session(S, None).unwrap();
    owner.submit(S, C, 0, "one", "Hello").unwrap();
    drop(owner);
    let mut owner = ExclusiveStore::open(&f.0, false).unwrap();
    assert_eq!(owner.backup_idle(), Err(Error::Busy));
    let recovery = owner.recover_cancel(S, 1).unwrap();
    assert_eq!(owner.backup_idle(), Err(Error::Busy));
    owner.observe_drained(&recovery).unwrap();
    owner.backup_idle().unwrap();
    assert_eq!(owner.backup_history(S).unwrap().0, 3);
}

#[test]
fn backup_never_replaces_stale_stage_or_symlink() {
    let f = Fixture::new();
    let owner = ExclusiveStore::open(&f.0, true).unwrap();
    let stage = f.0.join("backup.staging");
    fs::write(&stage, b"preserved").unwrap();
    assert_eq!(owner.backup_idle(), Err(Error::Database));
    assert_eq!(fs::read(&stage).unwrap(), b"preserved");
    fs::remove_file(&stage).unwrap();
    let target = f.0.join("target");
    fs::write(&target, b"unchanged").unwrap();
    symlink(&target, f.0.join("backup.sqlite")).unwrap();
    assert_eq!(owner.backup_idle(), Err(Error::Conflict));
    assert_eq!(owner.backup_history(S), Err(Error::Invalid));
    assert_eq!(fs::read(target).unwrap(), b"unchanged");
}

#[test]
#[ignore = "invoked by the parent process fixture"]
fn child_abrupt_exit() {
    let path = std::env::var_os("JARVIS_FIXTURE_ABRUPT_EXIT").expect("fixture path required");
    let mut owner = ExclusiveStore::open(&PathBuf::from(path), true).unwrap();
    owner.session(S, None).unwrap();
    assert!(matches!(
        owner.submit(S, C, 0, "one", "Hello"),
        Ok(Admission::New(_, _))
    ));
    // Skip destructors deliberately. The OS closes the lock descriptor.
    std::process::exit(0);
}

#[test]
fn abrupt_process_exit_releases_lock_but_not_durable_turn() {
    let f = Fixture::new();
    let status = Command::new(std::env::current_exe().unwrap())
        .args(["--ignored", "--exact", "child_abrupt_exit"])
        .env("JARVIS_FIXTURE_ABRUPT_EXIT", &f.0)
        .status()
        .unwrap();
    assert!(status.success());
    let mut owner = ExclusiveStore::open(&f.0, false).unwrap();
    assert_eq!(owner.recovery(S).unwrap().unwrap().state, State::Pending);
    assert_eq!(owner.backup_idle(), Err(Error::Busy));
    assert!(matches!(
        owner.submit(S, C, 0, "one", "Hello"),
        Ok(Admission::Replay(_))
    ));
    let recovery = owner.recover_cancel(S, 1).unwrap();
    assert_eq!(owner.observe_drained(&recovery), Ok(3));
}

#[test]
fn hardlinked_database_cannot_be_reopened_as_a_separate_owner() {
    let f = Fixture::new();
    drop(ExclusiveStore::open(&f.0, true).unwrap());
    let alias = f.0.join("alias.sqlite");
    fs::hard_link(f.0.join("fixture.sqlite"), &alias).unwrap();
    assert!(matches!(
        ExclusiveStore::open(&f.0, false),
        Err(Error::Invalid)
    ));
    fs::remove_file(alias).unwrap();
    assert!(ExclusiveStore::open(&f.0, false).is_ok());
}
