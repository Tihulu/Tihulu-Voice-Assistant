// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::budget::{self, Amount, Demand};
use jarvis_core::deadline;
use jarvis_core::session::{Error, Session, Terminal};

fn amount(ram: u64, vram: u64, generated: u64, prompt: u64) -> Amount {
    Amount {
        ram,
        vram,
        generated,
        prompt,
    }
}

fn demand(worker: &str) -> Demand {
    Demand {
        worker: worker.into(),
        amount: amount(16, 8, 16, 112),
    }
}

fn session() -> Session {
    Session::new(amount(32, 16, 64, 448), 100, 100, 20).unwrap()
}

#[test]
fn complete_wave_and_keep_token_charge() {
    let mut s = session();
    let rows = s
        .reserve_wave("one", &[demand("a"), demand("b")], 100)
        .unwrap();
    assert_eq!(s.snapshot().reserved, amount(32, 16, 32, 224));
    for row in &rows {
        s.dispatch(row, 100).unwrap();
    }
    assert_eq!(
        s.settle_reaped(&rows[0], 110, Some((8, 14))),
        Ok(Terminal::Measured)
    );
    assert_eq!(
        s.reserve_wave("two", &[demand("a")], 111).unwrap_err(),
        Error::Busy
    );
    assert_eq!(
        s.settle_reaped(&rows[1], 112, Some((16, 14))),
        Ok(Terminal::Measured)
    );
    assert_eq!(s.snapshot().spent, amount(0, 0, 24, 28));
    assert_eq!(s.snapshot().reserved, Amount::default());
    assert!(s.reserve_wave("two", &[demand("a")], 113).is_ok());
}

#[test]
fn deadline_retains_live_resources_and_drains() {
    let mut s = session();
    let rows = s
        .reserve_wave("one", &[demand("a"), demand("b")], 100)
        .unwrap();
    s.dispatch(&rows[0], 100).unwrap();
    assert_eq!(s.remaining(&rows[0], 119), Ok(1));
    assert_eq!(
        s.checkpoint(120),
        Err(Error::Deadline(deadline::Error::Expired))
    );
    assert_eq!(s.snapshot().reserved, amount(32, 16, 32, 224));
    assert_eq!(s.dispatch(&rows[1], 120), Err(Error::Closed));
    assert_eq!(s.void_unstarted(&rows[0]), Err(Error::WrongPhase));
    s.void_unstarted(&rows[1]).unwrap();
    assert_eq!(
        s.settle_reaped(&rows[0], 121, Some((10, 14))),
        Ok(Terminal::Drained)
    );
    assert!(s.drained());
    assert_eq!(s.snapshot().spent, amount(0, 0, 10, 14));
}

#[test]
fn invalid_usage_charges_both_ceilings_and_stops() {
    let mut s = session();
    let rows = s
        .reserve_wave("one", &[demand("a"), demand("b")], 100)
        .unwrap();
    s.dispatch(&rows[0], 100).unwrap();
    s.dispatch(&rows[1], 100).unwrap();
    assert_eq!(
        s.settle_reaped(&rows[0], 101, Some((17, 10))),
        Ok(Terminal::UnknownUsage)
    );
    assert_eq!(s.snapshot().spent, amount(0, 0, 16, 112));
    assert_eq!(s.snapshot().reserved, amount(16, 8, 16, 112));
    assert_eq!(
        s.settle_reaped(&rows[1], 102, None),
        Ok(Terminal::UnknownUsage)
    );
    assert!(s.drained());
}

#[test]
fn foreign_handle_cannot_poison_clock() {
    let mut s = session();
    let mut other = session();
    let a = s.reserve_wave("one", &[demand("a")], 100).unwrap();
    let b = other.reserve_wave("one", &[demand("a")], 100).unwrap();
    assert_eq!(s.dispatch(&b[0], 999), Err(Error::Foreign));
    s.dispatch(&a[0], 100).unwrap();
    assert_eq!(
        s.settle_reaped(&a[0], 101, Some((1, 1))),
        Ok(Terminal::Measured)
    );
    assert_eq!(s.settle_reaped(&a[0], 999, None), Err(Error::Foreign));
    assert!(!s.stopped());
}

#[test]
fn shrink_before_dispatch_stops_without_losing_reservation() {
    let mut s = session();
    let rows = s.reserve_wave("one", &[demand("a")], 100).unwrap();
    s.resize_memory(8, 8).unwrap();
    assert_eq!(
        s.dispatch(&rows[0], 101),
        Err(Error::Budget(budget::Error::Exhausted))
    );
    assert_eq!(s.snapshot().reserved, amount(16, 8, 16, 112));
    s.void_unstarted(&rows[0]).unwrap();
    assert!(s.drained());
}

#[test]
fn no_partial_wave_on_exhaustion() {
    let mut s = session();
    assert_eq!(
        s.reserve_wave("one", &[demand("a"), demand("b"), demand("c")], 100)
            .unwrap_err(),
        Error::Budget(budget::Error::Exhausted)
    );
    assert!(s.drained());
    assert_eq!(s.snapshot().reserved, Amount::default());
}

#[test]
fn prepared_handle_cannot_settle_and_manual_stop_drains() {
    let mut s = session();
    let rows = s.reserve_wave("one", &[demand("a")], 100).unwrap();
    assert_eq!(s.settle_reaped(&rows[0], 100, None), Err(Error::WrongPhase));
    assert_eq!(s.void_unstarted(&rows[0]), Err(Error::WrongPhase));
    s.stop();
    s.void_unstarted(&rows[0]).unwrap();
    assert!(s.drained());
}
