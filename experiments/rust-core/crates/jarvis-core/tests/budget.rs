// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::budget::{Amount, Demand, Error, Ledger};

fn a(ram: u64, vram: u64, generated: u64, prompt: u64) -> Amount {
    Amount { ram, vram, generated, prompt }
}
fn d(worker: &str, amount: Amount) -> Demand {
    Demand { worker: worker.into(), amount }
}
fn ledger() -> Ledger {
    Ledger::new(a(64, 16, 80, 600)).unwrap()
}
#[test]
fn four_dimensions_are_atomic() {
    for amount in [a(65, 0, 1, 1), a(1, 17, 1, 1), a(1, 0, 81, 1), a(1, 0, 1, 601)] {
        let mut l = ledger();
        let before = l.snapshot();
        assert_eq!(l.reserve_wave("a", &[d("x", a(1, 0, 1, 1)), d("y", amount)]).unwrap_err(), Error::Exhausted);
        assert_eq!(l.snapshot(), before);
        assert!(l.reserve_wave("a", &[d("x", a(1, 0, 1, 1))]).is_ok());
    }
}
#[test]
fn exact_boundary_and_measured_charge() {
    let mut l = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(32, 8, 40, 300)), d("y", a(32, 8, 40, 300))]).unwrap();
    assert_eq!(l.snapshot().reserved, a(64, 16, 80, 600));
    for row in &rows {
        l.dispatch(row).unwrap();
        l.settle_reaped(row, Some((10, 20))).unwrap();
    }
    assert_eq!(l.snapshot().reserved, Amount::default());
    assert_eq!(l.snapshot().spent, a(0, 0, 20, 40));
}
#[test]
fn cancellation_retains_until_reaped() {
    let mut l = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(32, 8, 16, 112))]).unwrap();
    l.dispatch(&rows[0]).unwrap();
    l.cancel(&rows[0]).unwrap();
    assert_eq!(l.snapshot().reserved, a(32, 8, 16, 112));
    assert_eq!(l.void_unstarted(&rows[0]), Err(Error::WrongPhase));
    l.settle_reaped(&rows[0], None).unwrap();
    assert_eq!(l.snapshot().spent, a(0, 0, 16, 112));
}
#[test]
fn invalid_measurement_never_refunds() {
    let mut l = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(32, 8, 16, 112))]).unwrap();
    l.dispatch(&rows[0]).unwrap();
    let before = l.snapshot();
    for usage in [(17, 14), (16, 113), (u64::MAX, 0)] {
        assert_eq!(l.settle_reaped(&rows[0], Some(usage)), Err(Error::Invalid));
        assert_eq!(l.snapshot(), before);
    }
    l.settle_reaped(&rows[0], None).unwrap();
}
#[test]
fn foreign_and_replayed_lease_denied() {
    let mut l = ledger();
    let mut other = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(1, 0, 1, 1))]).unwrap();
    let foreign = other.reserve_wave("a", &[d("x", a(1, 0, 1, 1))]).unwrap();
    assert_eq!(l.dispatch(&foreign[0]), Err(Error::Foreign));
    l.dispatch(&rows[0]).unwrap();
    l.settle_reaped(&rows[0], None).unwrap();
    assert_eq!(l.settle_reaped(&rows[0], None), Err(Error::Foreign));
    assert_eq!(l.reserve_wave("a", &[d("x", a(1, 0, 1, 1))]).unwrap_err(), Error::Duplicate);
}
#[test]
fn retry_keeps_previous_charge() {
    let mut l = ledger();
    let rows = l.reserve_wave("first", &[d("x", a(1, 0, 64, 448))]).unwrap();
    l.dispatch(&rows[0]).unwrap();
    l.settle_reaped(&rows[0], Some((16, 14))).unwrap();
    assert_eq!(l.reserve_wave("second", &[d("x", a(1, 0, 65, 448))]).unwrap_err(), Error::Exhausted);
    assert!(l.reserve_wave("second", &[d("x", a(1, 0, 64, 448))]).is_ok());
}
#[test]
fn void_only_cancelled_prepared() {
    let mut l = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(1, 0, 1, 1))]).unwrap();
    assert_eq!(l.void_unstarted(&rows[0]), Err(Error::WrongPhase));
    assert_eq!(l.settle_reaped(&rows[0], None), Err(Error::WrongPhase));
    l.cancel(&rows[0]).unwrap();
    assert_eq!(l.dispatch(&rows[0]), Err(Error::WrongPhase));
    l.void_unstarted(&rows[0]).unwrap();
    assert_eq!(l.snapshot().spent, Amount::default());
    assert_eq!(l.snapshot().reserved, Amount::default());
}
#[test]
fn resize_never_reclaims_live_memory() {
    let mut l = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(32, 8, 1, 1))]).unwrap();
    l.resize_memory(16, 4).unwrap();
    assert_eq!(l.snapshot().reserved, a(32, 8, 1, 1));
    assert_eq!(l.reserve_wave("b", &[d("y", a(1, 0, 1, 1))]).unwrap_err(), Error::Exhausted);
    l.cancel(&rows[0]).unwrap();
    l.void_unstarted(&rows[0]).unwrap();
}
#[test]
fn close_allows_only_drain() {
    let mut l = ledger();
    let rows = l.reserve_wave("a", &[d("x", a(1, 0, 1, 1)), d("y", a(1, 0, 1, 1))]).unwrap();
    l.dispatch(&rows[0]).unwrap();
    l.close();
    assert_eq!(l.dispatch(&rows[1]), Err(Error::Closed));
    assert_eq!(l.resize_memory(64, 16), Err(Error::Closed));
    assert_eq!(l.reserve_wave("b", &[d("z", a(1, 0, 1, 1))]).unwrap_err(), Error::Closed);
    l.void_unstarted(&rows[1]).unwrap();
    l.settle_reaped(&rows[0], None).unwrap();
    assert_eq!(l.snapshot().reserved, Amount::default());
}
#[test]
fn invalid_inputs_and_duplicate_workers() {
    let mut l = ledger();
    for worker in ["", "../escape", "a b", "é"] {
        assert_eq!(l.reserve_wave("a", &[d(worker, a(1, 0, 1, 1))]).unwrap_err(), Error::Invalid);
    }
    assert_eq!(l.reserve_wave("a", &[d("x", a(0, 0, 1, 1))]).unwrap_err(), Error::Invalid);
    assert_eq!(l.reserve_wave("a", &[d("x", a(1, 0, 1, 1)), d("x", a(1, 0, 1, 1))]).unwrap_err(), Error::Duplicate);
    assert!(l.reserve_wave("a", &[d("x", a(1, 0, 1, 1))]).is_ok());
    assert_eq!(l.reserve_wave("b", &[d("x", a(1, 0, 1, 1))]).unwrap_err(), Error::Duplicate);
}
#[test]
fn bounded_wave_and_lifetime() {
    let mut l = Ledger::new(a(1024, 0, 1024, 1024)).unwrap();
    let oversized: Vec<_> = (0..11).map(|i| d(&format!("x{i}"), a(1, 0, 1, 1))).collect();
    assert_eq!(l.reserve_wave("a", &oversized).unwrap_err(), Error::Invalid);
    for i in 0..1024 {
        let rows = l.reserve_wave(&format!("a{i}"), &[d("x", a(1, 0, 1, 1))]).unwrap();
        l.cancel(&rows[0]).unwrap();
        l.void_unstarted(&rows[0]).unwrap();
    }
    assert_eq!(l.reserve_wave("last", &[d("x", a(1, 0, 1, 1))]).unwrap_err(), Error::Limit);
}
