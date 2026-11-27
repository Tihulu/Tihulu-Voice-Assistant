// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::budget::{Amount, Demand};
use jarvis_core::harness::{self, Capability, Event, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::{Current, Error, HarnessWave, ResultState, Worker};
use jarvis_core::session::{self, Session};
use std::collections::BTreeSet;

fn amount(ram: u64, vram: u64, generated: u64, prompt: u64) -> Amount {
    Amount {
        ram,
        vram,
        generated,
        prompt,
    }
}

fn manifest(kind: Kind, protocol: Protocol, route: Route) -> Manifest {
    Manifest {
        kind,
        protocol,
        pin: [1; 32],
        supported: BTreeSet::from([Capability::Chat, Capability::Propose]),
        route,
    }
}

fn local() -> Manifest {
    manifest(Kind::OpenCode, Protocol::AcpV1, Route::Local)
}

fn cloud() -> Manifest {
    manifest(Kind::Codex, Protocol::CodexV1, Route::CloudPlan)
}

fn current(m: &Manifest) -> Current<'_> {
    Current {
        manifest: m,
        allowed: &m.supported,
        local_only: false,
    }
}

fn worker(name: &str, m: &Manifest) -> Worker {
    Worker {
        demand: Demand {
            worker: name.into(),
            amount: amount(16, 8, 16, 112),
        },
        plan: harness::negotiate(m, m, &m.supported, &m.supported, false).unwrap(),
    }
}

fn wave() -> HarnessWave {
    HarnessWave::new(Session::new(amount(32, 16, 64, 448), 100, 100, 20).unwrap()).unwrap()
}

#[test]
fn mixed_harnesses_share_one_ledger() {
    let mut w = wave();
    let a = local();
    let b = cloud();
    let keys = w
        .reserve("one", vec![worker("a", &a), worker("b", &b)], 100)
        .unwrap();
    assert_eq!(w.snapshot().reserved, amount(32, 16, 32, 224));
    for (key, m) in [(&keys[0], &a), (&keys[1], &b)] {
        w.dispatch(key, &current(m), 100).unwrap();
    }
    for (key, m) in [(&keys[0], &a), (&keys[1], &b)] {
        w.record(key, &current(m), 101, 1, Event::Started).unwrap();
        w.record(key, &current(m), 101, 2, Event::ReportedDone)
            .unwrap();
    }
    assert_eq!(w.snapshot().reserved, amount(32, 16, 32, 224));
    assert_eq!(
        w.settle(&keys[0], &current(&a), 102, Some((8, 14))),
        Ok(ResultState::MeasuredReport)
    );
    assert!(matches!(
        w.reserve("two", vec![worker("a", &a)], 103),
        Err(Error::Phase)
    ));
    assert_eq!(
        w.settle(&keys[1], &current(&b), 103, Some((16, 14))),
        Ok(ResultState::MeasuredReport)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    assert_eq!(w.snapshot().spent, amount(0, 0, 24, 28));
    assert!(w.reserve("two", vec![worker("a", &a)], 104).is_ok());
}

#[test]
fn drift_after_peer_dispatch_retains_all_until_exact_cleanup() {
    let mut w = wave();
    let a = local();
    let b = cloud();
    let keys = w
        .reserve("one", vec![worker("a", &a), worker("b", &b)], 100)
        .unwrap();
    w.dispatch(&keys[0], &current(&a), 100).unwrap();
    let changed = Manifest { pin: [2; 32], ..b };
    assert_eq!(
        w.dispatch(&keys[1], &current(&changed), 101),
        Err(Error::Harness(harness::Error::Drift))
    );
    assert_eq!(w.snapshot().reserved, amount(32, 16, 32, 224));
    assert_eq!(w.void_unstarted(&keys[0]), Err(Error::Phase));
    w.void_unstarted(&keys[1]).unwrap();
    assert_eq!(
        w.settle(&keys[0], &current(&a), 102, None),
        Ok(ResultState::NoPublication)
    );
    assert_eq!(w.snapshot().spent, amount(0, 0, 16, 112));
    assert!(w.drained());
}

#[test]
fn policy_revocation_after_report_denies_success() {
    let mut w = wave();
    let a = local();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    let key = &keys[0];
    w.dispatch(key, &current(&a), 100).unwrap();
    w.record(key, &current(&a), 101, 1, Event::Started).unwrap();
    w.record(key, &current(&a), 102, 2, Event::ReportedDone)
        .unwrap();
    let revoked = Current {
        manifest: &a,
        allowed: &BTreeSet::new(),
        local_only: true,
    };
    assert_eq!(
        w.settle(key, &revoked, 103, Some((1, 2))),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
    assert_eq!(w.snapshot().spent, amount(0, 0, 1, 2));
}

#[test]
fn foreign_key_cannot_mutate_clock_or_budget() {
    let a = local();
    let mut w = wave();
    let mut other = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    let foreign = other.reserve("one", vec![worker("a", &a)], 100).unwrap();
    assert_eq!(
        w.dispatch(&foreign[0], &current(&a), 999),
        Err(Error::Foreign)
    );
    assert_eq!(
        w.settle(&foreign[0], &current(&a), 999, None),
        Err(Error::Foreign)
    );
    w.dispatch(&keys[0], &current(&a), 100).unwrap();
    assert!(!w.stopped());
}

#[test]
fn approval_request_stops_without_implicit_permission_or_release() {
    let a = local();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    let key = &keys[0];
    w.dispatch(key, &current(&a), 100).unwrap();
    w.record(key, &current(&a), 101, 1, Event::Started).unwrap();
    w.record(key, &current(&a), 102, 2, Event::ApprovalNeeded)
        .unwrap();
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount(16, 8, 16, 112));
    assert_eq!(
        w.record(key, &current(&a), 103, 3, Event::ReportedDone),
        Err(Error::Session(session::Error::Closed))
    );
    assert_eq!(
        w.settle(key, &current(&a), 104, Some((2, 3))),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn timeout_and_exit_without_report_never_count_as_success() {
    let a = local();
    for report in [true, false] {
        let mut w = wave();
        let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
        let key = &keys[0];
        w.dispatch(key, &current(&a), 100).unwrap();
        if report {
            w.record(key, &current(&a), 101, 1, Event::Started).unwrap();
            w.record(key, &current(&a), 102, 2, Event::ReportedDone)
                .unwrap();
        }
        assert_eq!(
            w.settle(
                key,
                &current(&a),
                if report { 120 } else { 103 },
                Some((2, 3))
            ),
            Ok(ResultState::NoPublication)
        );
        assert!(w.drained());
    }
}

#[test]
fn capacity_shrink_and_oversized_wave_cannot_dispatch() {
    let a = local();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    w.resize_memory(8, 8).unwrap();
    assert!(matches!(
        w.dispatch(&keys[0], &current(&a), 101),
        Err(Error::Session(_))
    ));
    assert_eq!(w.snapshot().reserved, amount(16, 8, 16, 112));
    w.void_unstarted(&keys[0]).unwrap();
    assert!(w.drained());
    let mut w = wave();
    assert!(matches!(
        w.reserve(
            "one",
            vec![worker("a", &a), worker("b", &a), worker("c", &a)],
            100
        ),
        Err(Error::Session(_))
    ));
    assert_eq!(w.snapshot().reserved, Amount::default());
}

#[test]
fn local_only_change_blocks_cloud_and_duplicate_event_stops_wave() {
    let b = cloud();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("b", &b)], 100).unwrap();
    let strict = Current {
        manifest: &b,
        allowed: &b.supported,
        local_only: true,
    };
    assert_eq!(
        w.dispatch(&keys[0], &strict, 100),
        Err(Error::Harness(harness::Error::Denied))
    );
    w.void_unstarted(&keys[0]).unwrap();
    let a = local();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    let key = &keys[0];
    w.dispatch(key, &current(&a), 100).unwrap();
    w.record(key, &current(&a), 101, 1, Event::Started).unwrap();
    assert_eq!(
        w.record(key, &current(&a), 102, 1, Event::Progress),
        Err(Error::Harness(harness::Error::Sequence))
    );
    assert!(w.stopped());
    assert_eq!(
        w.settle(key, &current(&a), 103, Some((1, 1))),
        Ok(ResultState::NoPublication)
    );
}

#[test]
fn clock_regression_keeps_peer_reservations_until_cleanup() {
    let a = local();
    let mut w = wave();
    let keys = w
        .reserve("one", vec![worker("a", &a), worker("b", &a)], 100)
        .unwrap();
    w.dispatch(&keys[0], &current(&a), 100).unwrap();
    w.record(&keys[0], &current(&a), 101, 1, Event::Started)
        .unwrap();
    assert_eq!(
        w.dispatch(&keys[1], &current(&a), 100),
        Err(Error::Session(session::Error::Deadline(
            jarvis_core::deadline::Error::ClockRegression
        )))
    );
    assert_eq!(w.snapshot().reserved, amount(32, 16, 32, 224));
    w.void_unstarted(&keys[1]).unwrap();
    assert_eq!(
        w.settle(&keys[0], &current(&a), 102, Some((1, 1))),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn active_capacity_shrink_stops_peers_and_retains_exact_reservations() {
    let a = local();
    for (ram, vram) in [(31, 16), (32, 15)] {
        let mut w = wave();
        let keys = w
            .reserve("one", vec![worker("a", &a), worker("b", &a)], 100)
            .unwrap();
        w.dispatch(&keys[0], &current(&a), 100).unwrap();
        w.record(&keys[0], &current(&a), 101, 1, Event::Started)
            .unwrap();
        w.resize_memory(ram, vram).unwrap();
        assert!(w.stopped());
        assert_eq!(w.snapshot().reserved, amount(32, 16, 32, 224));
        assert_eq!(
            w.dispatch(&keys[1], &current(&a), 102),
            Err(Error::Session(session::Error::Closed))
        );
        assert_eq!(
            w.record(&keys[0], &current(&a), 102, 2, Event::ReportedDone),
            Err(Error::Session(session::Error::Closed))
        );
        assert_eq!(w.void_unstarted(&keys[0]), Err(Error::Phase));
        w.void_unstarted(&keys[1]).unwrap();
        assert_eq!(
            w.settle(&keys[0], &current(&a), 103, Some((2, 3))),
            Ok(ResultState::NoPublication)
        );
        assert_eq!(w.snapshot().reserved, Amount::default());
        assert!(w.drained());
    }
}

#[test]
fn capacity_shrink_after_report_cannot_keep_success() {
    let a = local();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    let key = &keys[0];
    w.dispatch(key, &current(&a), 100).unwrap();
    w.record(key, &current(&a), 101, 1, Event::Started).unwrap();
    w.record(key, &current(&a), 102, 2, Event::ReportedDone)
        .unwrap();
    w.resize_memory(15, 8).unwrap();
    assert_eq!(w.snapshot().reserved, amount(16, 8, 16, 112));
    assert_eq!(
        w.settle(key, &current(&a), 103, Some((2, 3))),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn capacity_equal_to_reservation_does_not_stop() {
    let a = local();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    let key = &keys[0];
    w.dispatch(key, &current(&a), 100).unwrap();
    w.resize_memory(16, 8).unwrap();
    assert!(!w.stopped());
    w.record(key, &current(&a), 101, 1, Event::Started).unwrap();
    w.record(key, &current(&a), 102, 2, Event::ReportedDone)
        .unwrap();
    assert_eq!(
        w.settle(key, &current(&a), 103, Some((2, 3))),
        Ok(ResultState::MeasuredReport)
    );
}

#[test]
fn heartbeat_expires_silent_worker_and_preserves_pending_peer() {
    let a = local();
    let mut w = wave();
    let keys = w
        .reserve("one", vec![worker("a", &a), worker("b", &a)], 100)
        .unwrap();
    w.dispatch(&keys[0], &current(&a), 100).unwrap();
    w.tick(119).unwrap();
    assert_eq!(
        w.tick(120),
        Err(Error::Session(session::Error::Deadline(
            jarvis_core::deadline::Error::Expired
        )))
    );
    assert_eq!(w.snapshot().reserved, amount(32, 16, 32, 224));
    assert!(!w.drained());
    w.void_unstarted(&keys[1]).unwrap();
    assert_eq!(
        w.settle(&keys[0], &current(&a), 121, None),
        Ok(ResultState::NoPublication)
    );
    assert_eq!(w.snapshot().spent, amount(0, 0, 16, 112));
    assert!(w.drained());
}

#[test]
fn heartbeat_expires_undispatched_wave_at_total_deadline() {
    let a = local();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &a)], 100).unwrap();
    w.tick(199).unwrap();
    assert_eq!(
        w.tick(200),
        Err(Error::Session(session::Error::Deadline(
            jarvis_core::deadline::Error::Expired
        )))
    );
    assert_eq!(w.snapshot().reserved, amount(16, 8, 16, 112));
    w.void_unstarted(&keys[0]).unwrap();
    assert_eq!(w.snapshot().spent, Amount::default());
    assert!(w.drained());
}
