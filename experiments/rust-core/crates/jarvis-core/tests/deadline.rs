// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::deadline::{Deadlines, Error};

#[test]
fn exact_worker_boundary_is_terminal() {
    let mut clock = Deadlines::new(100, 100, 20).unwrap();
    let ticket = clock.start(100).unwrap();
    assert_eq!(clock.remaining(&ticket, 119), Ok(1));
    assert_eq!(clock.check(120), Err(Error::Expired));
    clock.finish(&ticket).unwrap();
    assert_eq!(clock.start(121).unwrap_err(), Error::Expired);
}

#[test]
fn session_deadline_caps_worker() {
    let mut clock = Deadlines::new(100, 100, 20).unwrap();
    let ticket = clock.start(195).unwrap();
    assert_eq!(clock.remaining(&ticket, 199), Ok(1));
    assert_eq!(clock.check(200), Err(Error::Expired));
}

#[test]
fn regression_is_sticky_and_drain_allowed() {
    let mut clock = Deadlines::new(100, 100, 20).unwrap();
    let ticket = clock.start(110).unwrap();
    assert_eq!(clock.check(109), Err(Error::ClockRegression));
    assert_eq!(clock.check(111), Err(Error::ClockRegression));
    clock.finish(&ticket).unwrap();
    assert_eq!(clock.check(112), Err(Error::ClockRegression));
}

#[test]
fn foreign_and_replayed_tickets_denied() {
    let mut a = Deadlines::new(100, 100, 20).unwrap();
    let mut b = Deadlines::new(100, 100, 20).unwrap();
    let x = a.start(100).unwrap();
    let y = b.start(100).unwrap();
    assert_eq!(a.remaining(&y, 999), Err(Error::Foreign));
    assert_eq!(a.remaining(&x, 100), Ok(20));
    a.finish(&x).unwrap();
    assert_eq!(a.finish(&x), Err(Error::Foreign));
    assert_eq!(a.finish(&y), Err(Error::Foreign));
}

#[test]
fn invalid_limits_and_overflow_denied() {
    for (now, total, worker) in [
        (100, 0, 0),
        (100, 20, 0),
        (100, 20, 21),
        (100, 3_600_000_000_001, 1),
        (u64::MAX, 1, 1),
    ] {
        assert!(matches!(
            Deadlines::new(now, total, worker),
            Err(Error::Invalid)
        ));
    }
}

#[test]
fn parallel_expiry_prevents_new_work() {
    let mut clock = Deadlines::new(100, 100, 20).unwrap();
    let first = clock.start(100).unwrap();
    let second = clock.start(105).unwrap();
    assert_eq!(clock.remaining(&second, 119), Ok(6));
    assert_eq!(clock.remaining(&second, 120), Err(Error::Expired));
    clock.finish(&first).unwrap();
    clock.finish(&second).unwrap();
    assert_eq!(clock.start(121).unwrap_err(), Error::Expired);
}

#[test]
fn ticket_count_is_bounded() {
    let mut clock = Deadlines::new(100, 100, 20).unwrap();
    let tickets: Vec<_> = (0..64).map(|_| clock.start(100).unwrap()).collect();
    assert_eq!(clock.start(100).unwrap_err(), Error::Limit);
    for ticket in tickets {
        clock.finish(&ticket).unwrap();
    }
    for _ in 64..1024 {
        let ticket = clock.start(100).unwrap();
        clock.finish(&ticket).unwrap();
    }
    assert_eq!(clock.start(100).unwrap_err(), Error::Limit);
}
