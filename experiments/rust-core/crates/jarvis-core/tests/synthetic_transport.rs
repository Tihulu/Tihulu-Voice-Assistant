// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::budget::{Amount, Demand};
use jarvis_core::harness::{self, Capability, Event, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::{Current, Error, HarnessWave, PolicyStamp, ResultState, Worker};
use jarvis_core::session::Session;
use jarvis_core::synthetic_transport::{self, Identity, FRAME_LEN};
use std::collections::BTreeSet;

fn manifest() -> Manifest {
    Manifest {
        kind: Kind::OpenCode,
        protocol: Protocol::AcpV1,
        pin: [1; 32],
        supported: BTreeSet::from([Capability::Chat, Capability::Cancel]),
        route: Route::Local,
    }
}

fn current(m: &Manifest) -> Current<'_> {
    Current {
        manifest: m,
        allowed: &m.supported,
        policy: PolicyStamp {
            generation: 1,
            local_only: true,
        },
    }
}

fn amount() -> Amount {
    Amount {
        ram: 16,
        vram: 8,
        generated: 16,
        prompt: 112,
    }
}

fn worker<'a>(name: &str, m: &'a Manifest) -> Worker<'a> {
    Worker {
        demand: Demand {
            worker: name.into(),
            amount: amount(),
        },
        plan: harness::negotiate(m, m, &m.supported, &m.supported, true).unwrap(),
        current: current(m),
    }
}

fn wave() -> HarnessWave {
    HarnessWave::new(Session::new(amount(), 100, 100, 20).unwrap()).unwrap()
}

fn identity() -> Identity {
    Identity {
        project: [1; 32],
        session: [2; 32],
        attempt: [3; 32],
        worker: [4; 32],
        connection: [5; 32],
    }
}

fn frame(id: Identity, seq: u64, event: u8) -> [u8; FRAME_LEN] {
    let mut b = [0; FRAME_LEN];
    b[..4].copy_from_slice(b"JHF1");
    b[4] = 1;
    b[5] = event;
    b[6..14].copy_from_slice(&seq.to_be_bytes());
    b[14..46].copy_from_slice(&id.project);
    b[46..78].copy_from_slice(&id.session);
    b[78..110].copy_from_slice(&id.attempt);
    b[110..142].copy_from_slice(&id.worker);
    b[142..174].copy_from_slice(&id.connection);
    b
}

#[test]
fn exact_envelope_uses_existing_wave_and_requires_trusted_settle() {
    let m = manifest();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &m)], 100).unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    assert_eq!(
        w.record(key, &current(&m), 101, 1, Event::Started),
        Err(Error::Phase)
    );
    w.record_frame(key, &current(&m), 101, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, &frame(id, 2, 4))
        .unwrap();
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle(key, &current(&m), 103, Some((1, 2))),
        Ok(ResultState::MeasuredReport)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
}

#[test]
fn strict_decoder_rejects_length_version_event_and_zero_identity() {
    let id = identity();
    let mut b = frame(id, 1, 1);
    assert_eq!(
        synthetic_transport::decode(&b[..173]),
        Err(synthetic_transport::Error::Length)
    );
    let mut extra = b.to_vec();
    extra.push(0);
    assert_eq!(
        synthetic_transport::decode(&extra),
        Err(synthetic_transport::Error::Length)
    );
    b[4] = 3;
    assert_eq!(
        synthetic_transport::decode(&b),
        Err(synthetic_transport::Error::Version)
    );
    b[4] = 1;
    b[5] = 9;
    assert_eq!(
        synthetic_transport::decode(&b),
        Err(synthetic_transport::Error::Event)
    );
    b = frame(id, 0, 1);
    assert_eq!(
        synthetic_transport::decode(&b),
        Err(synthetic_transport::Error::Sequence)
    );
    let empty = Identity {
        worker: [0; 32],
        ..id
    };
    assert_eq!(
        synthetic_transport::decode(&frame(empty, 1, 1)),
        Err(synthetic_transport::Error::Identity)
    );
}

#[test]
fn foreign_envelope_stops_without_releasing_reservation() {
    let m = manifest();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &m)], 100).unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let foreign = Identity {
        session: [9; 32],
        ..id
    };
    assert_eq!(
        w.record_frame(key, &current(&m), 101, &frame(foreign, 1, 1)),
        Err(Error::Transport(synthetic_transport::Error::Identity))
    );
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle(key, &current(&m), 102, None),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn gap_or_replay_stops_existing_event_tracker() {
    let m = manifest();
    for seq in [1, 3] {
        let mut w = wave();
        let keys = w.reserve("one", vec![worker("a", &m)], 100).unwrap();
        let key = &keys[0];
        let id = identity();
        w.bind_transport(key, &current(&m), id, 100).unwrap();
        w.dispatch(key, &current(&m), 100).unwrap();
        w.record_frame(key, &current(&m), 101, &frame(id, 1, 1))
            .unwrap();
        assert_eq!(
            w.record_frame(key, &current(&m), 102, &frame(id, seq, 2)),
            Err(Error::Harness(harness::Error::Sequence))
        );
        assert_eq!(w.snapshot().reserved, amount());
        assert!(w.stopped());
    }
}

#[test]
fn disconnect_after_report_cannot_count_as_success() {
    let m = manifest();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &m)], 100).unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, &frame(id, 2, 4))
        .unwrap();
    let wrong = Identity {
        connection: [9; 32],
        ..id
    };
    assert_eq!(w.disconnect_transport(key, wrong), Err(Error::Foreign));
    assert!(!w.stopped());
    w.disconnect_transport(key, id).unwrap();
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle(key, &current(&m), 103, Some((1, 2))),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn approval_frame_stops_wave_without_implicit_resume() {
    let m = manifest();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &m)], 100).unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, &frame(id, 2, 3))
        .unwrap();
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
    assert!(w
        .record_frame(key, &current(&m), 103, &frame(id, 3, 4))
        .is_err());
}

#[test]
fn connection_id_cannot_be_reused_after_successful_settle() {
    let m = manifest();
    let mut w = wave();
    let keys = w.reserve("one", vec![worker("a", &m)], 100).unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, &frame(id, 2, 4))
        .unwrap();
    w.settle(key, &current(&m), 103, Some((0, 0))).unwrap();
    let keys = w.reserve("two", vec![worker("a", &m)], 104).unwrap();
    assert_eq!(
        w.bind_transport(&keys[0], &current(&m), id, 104),
        Err(Error::Transport(synthetic_transport::Error::Identity))
    );
    w.void_unstarted(&keys[0]).unwrap();
    assert!(w.drained());
}
