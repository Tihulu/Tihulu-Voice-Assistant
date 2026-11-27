// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::budget::{Amount, Demand};
use jarvis_core::harness::{self, Capability, Event, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::{Current, Error, HarnessWave, PolicyStamp, ResultState, Worker};
use jarvis_core::session::Session;
use jarvis_core::synthetic_transport::{self, Identity, FRAME_LEN};
use std::collections::{BTreeMap, BTreeSet};

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
    HarnessWave::new_scoped(
        Session::new(amount(), 100, 100, 20).unwrap(),
        [1; 32],
        [2; 32],
    )
    .unwrap()
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

fn plan_one(id: Identity) -> BTreeMap<String, Identity> {
    BTreeMap::from([("a".into(), id)])
}

fn plan_pair() -> BTreeMap<String, Identity> {
    let a = identity();
    let b = Identity {
        worker: [6; 32],
        connection: [7; 32],
        ..a
    };
    BTreeMap::from([("a".into(), a), ("b".into(), b)])
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
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    assert_eq!(
        w.record(key, &current(&m), 101, 1, Event::Started),
        Err(Error::Phase)
    );
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 103, id, Some((1, 2))),
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
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let foreign = Identity {
        session: [9; 32],
        ..id
    };
    assert_eq!(
        w.record_frame(key, &current(&m), 101, id, &frame(foreign, 1, 1)),
        Err(Error::Transport(synthetic_transport::Error::Identity))
    );
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 102, id, None),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn gap_or_replay_stops_existing_event_tracker() {
    let m = manifest();
    for seq in [1, 3] {
        let mut w = wave();
        let keys = w
            .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
            .unwrap();
        let key = &keys[0];
        let id = identity();
        w.bind_transport(key, &current(&m), id, 100).unwrap();
        w.dispatch(key, &current(&m), 100).unwrap();
        w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
            .unwrap();
        assert_eq!(
            w.record_frame(key, &current(&m), 102, id, &frame(id, seq, 2)),
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
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
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
        w.settle_transport(key, &current(&m), 103, id, Some((1, 2))),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn approval_frame_stops_wave_without_implicit_resume() {
    let m = manifest();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 3))
        .unwrap();
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
    assert!(w
        .record_frame(key, &current(&m), 103, id, &frame(id, 3, 4))
        .is_err());
}

#[test]
fn connection_id_cannot_be_reused_after_successful_settle() {
    let m = manifest();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    w.settle_transport(key, &current(&m), 103, id, Some((0, 0)))
        .unwrap();
    let fresh_attempt = Identity {
        attempt: [8; 32],
        ..id
    };
    assert_eq!(
        w.reserve_with_transport("two", vec![worker("a", &m)], plan_one(fresh_attempt), 104)
            .unwrap_err(),
        Error::Transport(synthetic_transport::Error::Identity)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    assert!(w.drained());
}

fn pair_wave() -> HarnessWave {
    let capacity = Amount {
        ram: 32,
        vram: 16,
        generated: 32,
        prompt: 224,
    };
    HarnessWave::new_scoped(
        Session::new(capacity, 100, 100, 20).unwrap(),
        [1; 32],
        [2; 32],
    )
    .unwrap()
}

#[test]
fn acp_worker_cannot_dispatch_without_bound_envelope() {
    let m = manifest();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    assert_eq!(w.dispatch(&keys[0], &current(&m), 100), Err(Error::Phase));
    assert_eq!(w.snapshot().reserved, amount());
    w.void_transport_unstarted(&keys[0], identity()).unwrap();
    assert!(w.drained());
}

#[test]
fn all_acp_peers_must_bind_before_any_dispatch() {
    let m = manifest();
    let mut w = pair_wave();
    let keys = w
        .reserve_with_transport(
            "one",
            vec![worker("a", &m), worker("b", &m)],
            plan_pair(),
            100,
        )
        .unwrap();
    w.bind_transport(&keys[0], &current(&m), identity(), 100)
        .unwrap();
    assert_eq!(w.dispatch(&keys[0], &current(&m), 100), Err(Error::Phase));
    assert!(w.stopped());
    w.void_transport_unstarted(&keys[0], identity()).unwrap();
    w.void_transport_unstarted(&keys[1], plan_pair()["b"])
        .unwrap();
    assert!(w.drained());
}

#[test]
fn distinct_peers_share_exact_project_session_and_attempt() {
    let m = manifest();
    let mut w = pair_wave();
    let keys = w
        .reserve_with_transport(
            "one",
            vec![worker("a", &m), worker("b", &m)],
            plan_pair(),
            100,
        )
        .unwrap();
    let a = identity();
    let b = Identity {
        worker: [6; 32],
        connection: [7; 32],
        ..a
    };
    w.bind_transport(&keys[0], &current(&m), a, 100).unwrap();
    w.bind_transport(&keys[1], &current(&m), b, 100).unwrap();
    w.dispatch(&keys[0], &current(&m), 100).unwrap();
    w.dispatch(&keys[1], &current(&m), 100).unwrap();
    w.record_frame(&keys[0], &current(&m), 101, a, &frame(a, 1, 1))
        .unwrap();
    w.record_frame(&keys[1], &current(&m), 101, b, &frame(b, 1, 1))
        .unwrap();
    assert!(!w.stopped());
}

#[test]
fn peer_scope_drift_or_duplicate_worker_rejects_entire_wave() {
    let m = manifest();
    let a = identity();
    let b = Identity {
        worker: [6; 32],
        connection: [7; 32],
        ..a
    };
    for invalid in [
        Identity {
            project: [9; 32],
            ..b
        },
        Identity {
            session: [9; 32],
            ..b
        },
        Identity {
            attempt: [9; 32],
            ..b
        },
        Identity {
            worker: a.worker,
            ..b
        },
    ] {
        let mut w = pair_wave();
        let keys = w
            .reserve_with_transport(
                "one",
                vec![worker("a", &m), worker("b", &m)],
                plan_pair(),
                100,
            )
            .unwrap();
        w.bind_transport(&keys[0], &current(&m), a, 100).unwrap();
        assert_eq!(
            w.bind_transport(&keys[1], &current(&m), invalid, 100),
            Err(Error::Transport(synthetic_transport::Error::Identity))
        );
        assert!(w.stopped());
        w.void_transport_unstarted(&keys[0], identity()).unwrap();
        w.void_transport_unstarted(&keys[1], plan_pair()["b"])
            .unwrap();
        assert!(w.drained());
    }
}

#[test]
fn version_mismatch_is_not_a_compatible_fallback() {
    let m = manifest();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let mut bytes = frame(id, 1, 1);
    bytes[4] = 2;
    assert_eq!(
        w.record_frame(key, &current(&m), 101, id, &bytes),
        Err(Error::Transport(synthetic_transport::Error::Identity))
    );
    assert_eq!(w.snapshot().reserved, amount());
    assert!(w.stopped());
}

#[test]
fn old_attempt_cannot_be_rebound_with_fresh_connection() {
    let m = manifest();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    let id = identity();
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    w.settle_transport(key, &current(&m), 103, id, Some((0, 0)))
        .unwrap();
    let fresh = Identity {
        connection: [8; 32],
        ..id
    };
    assert_eq!(
        w.reserve_with_transport("two", vec![worker("a", &m)], plan_one(fresh), 104)
            .unwrap_err(),
        Error::Transport(synthetic_transport::Error::Identity)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    assert!(w.drained());
}

#[test]
fn split_frames_at_every_byte_boundary() {
    let m = manifest();
    let id = identity();
    for split in 1..FRAME_LEN {
        let mut w = wave();
        let keys = w
            .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
            .unwrap();
        let key = &keys[0];
        w.bind_transport(key, &current(&m), id, 100).unwrap();
        w.dispatch(key, &current(&m), 100).unwrap();
        let start = frame(id, 1, 1);
        w.record_chunk(key, &current(&m), 101, id, &start[..split])
            .unwrap();
        w.record_chunk(key, &current(&m), 102, id, &start[split..])
            .unwrap();
        w.record_chunk(key, &current(&m), 103, id, &frame(id, 2, 4))
            .unwrap();
        assert_eq!(
            w.settle_transport(key, &current(&m), 104, id, Some((0, 0))),
            Ok(ResultState::MeasuredReport)
        );
    }
}

#[test]
fn coalesced_frames_share_existing_sequence_and_reservation() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let bytes = [frame(id, 1, 1), frame(id, 2, 2), frame(id, 3, 4)].concat();
    w.record_chunk(key, &current(&m), 101, id, &bytes).unwrap();
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 102, id, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
}

#[test]
fn raw_full_frame_cannot_skip_a_buffered_prefix() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_chunk(key, &current(&m), 101, id, &frame(id, 1, 1)[..5])
        .unwrap();
    assert_eq!(
        w.record_frame(key, &current(&m), 102, id, &frame(id, 1, 1)),
        Err(Error::Transport(synthetic_transport::Error::Partial))
    );
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
}

#[test]
fn oversized_chunk_fails_closed_before_processing_any_frame() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let bytes = vec![0; synthetic_transport::MAX_CHUNK + 1];
    assert_eq!(
        w.record_chunk(key, &current(&m), 101, id, &bytes),
        Err(Error::Transport(synthetic_transport::Error::ChunkLimit))
    );
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
}

#[test]
fn partial_eof_requires_exact_trusted_cleanup() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_chunk(key, &current(&m), 101, id, &frame(id, 1, 1)[..5])
        .unwrap();
    assert_eq!(
        w.disconnect_transport(key, id),
        Err(Error::Transport(synthetic_transport::Error::Partial))
    );
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 102, id, None),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn trailing_bytes_after_done_in_same_chunk_invalidate_report() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let mut bytes = [frame(id, 1, 1), frame(id, 2, 4)].concat();
    bytes.push(0);
    assert_eq!(
        w.record_chunk(key, &current(&m), 101, id, &bytes),
        Err(Error::Phase)
    );
    assert_eq!(
        w.settle_transport(key, &current(&m), 102, id, Some((0, 0))),
        Ok(ResultState::NoPublication)
    );
}

#[test]
fn exact_owner_cancel_retains_lease_and_rejects_foreign_identity() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let foreign = Identity {
        connection: [9; 32],
        ..id
    };
    assert_eq!(w.cancel_transport(key, foreign), Err(Error::Foreign));
    assert!(!w.stopped());
    w.cancel_transport(key, id).unwrap();
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 102, id, None),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn foreign_chunk_cannot_mutate_partial_buffer_or_clock() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let start = frame(id, 1, 1);
    w.record_chunk(key, &current(&m), 101, id, &start[..5])
        .unwrap();
    let foreign = Identity {
        connection: [9; 32],
        ..id
    };
    assert_eq!(
        w.record_chunk(key, &current(&m), 999, foreign, &[0; 12]),
        Err(Error::Foreign)
    );
    w.record_chunk(key, &current(&m), 102, id, &start[5..])
        .unwrap();
    w.record_chunk(key, &current(&m), 103, id, &frame(id, 2, 4))
        .unwrap();
    assert_eq!(
        w.settle_transport(key, &current(&m), 104, id, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
}

#[test]
fn partial_buffers_remain_isolated_between_concurrent_peers() {
    let m = manifest();
    let mut w = pair_wave();
    let keys = w
        .reserve_with_transport(
            "one",
            vec![worker("a", &m), worker("b", &m)],
            plan_pair(),
            100,
        )
        .unwrap();
    let a = identity();
    let b = Identity {
        worker: [6; 32],
        connection: [7; 32],
        ..a
    };
    w.bind_transport(&keys[0], &current(&m), a, 100).unwrap();
    w.bind_transport(&keys[1], &current(&m), b, 100).unwrap();
    w.dispatch(&keys[0], &current(&m), 100).unwrap();
    w.dispatch(&keys[1], &current(&m), 100).unwrap();
    let first = frame(a, 1, 1);
    let second = frame(b, 1, 1);
    w.record_chunk(&keys[0], &current(&m), 101, a, &first[..3])
        .unwrap();
    w.record_chunk(&keys[1], &current(&m), 101, b, &second[..99])
        .unwrap();
    w.record_chunk(&keys[0], &current(&m), 102, a, &first[3..])
        .unwrap();
    w.record_chunk(&keys[1], &current(&m), 102, b, &second[99..])
        .unwrap();
    w.record_chunk(&keys[0], &current(&m), 103, a, &frame(a, 2, 4))
        .unwrap();
    w.record_chunk(&keys[1], &current(&m), 103, b, &frame(b, 2, 4))
        .unwrap();
    assert_eq!(
        w.settle_transport(&keys[0], &current(&m), 104, a, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
    assert_eq!(
        w.settle_transport(&keys[1], &current(&m), 104, b, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    assert!(!w.stopped());
    assert!(!w.drained());
    w.stop();
    assert!(w.drained());
}

#[test]
fn partial_frame_does_not_extend_worker_deadline() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let start = frame(id, 1, 1);
    w.record_chunk(key, &current(&m), 119, id, &start[..5])
        .unwrap();
    assert!(w
        .record_chunk(key, &current(&m), 120, id, &start[5..])
        .is_err());
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 121, id, None),
        Ok(ResultState::NoPublication)
    );
}

#[test]
fn malformed_second_frame_invalidates_coalesced_report() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let mut bad = frame(id, 2, 4);
    bad[0] = 0;
    let bytes = [frame(id, 1, 1), bad].concat();
    assert_eq!(
        w.record_chunk(key, &current(&m), 101, id, &bytes),
        Err(Error::Transport(synthetic_transport::Error::Version))
    );
    assert!(w.stopped());
    assert_eq!(
        w.settle_transport(key, &current(&m), 102, id, Some((0, 0))),
        Ok(ResultState::NoPublication)
    );
}

#[test]
fn foreign_full_frame_source_cannot_mutate_buffer_clock_or_policy() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    let start = frame(id, 1, 1);
    w.record_chunk(key, &current(&m), 101, id, &start[..5])
        .unwrap();
    let wrong = Identity {
        connection: [9; 32],
        ..id
    };
    assert_eq!(
        w.record_frame(key, &current(&m), 999, wrong, &start),
        Err(Error::Foreign)
    );
    assert!(!w.stopped());
    w.record_chunk(key, &current(&m), 102, id, &start[5..])
        .unwrap();
    w.record_frame(key, &current(&m), 103, id, &frame(id, 2, 4))
        .unwrap();
    assert_eq!(
        w.settle_transport(key, &current(&m), 104, id, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
}

#[test]
fn foreign_terminal_observation_cannot_release_exact_reservation() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    let wrong = Identity {
        worker: [9; 32],
        ..id
    };
    assert_eq!(
        w.settle_transport(key, &current(&m), 999, wrong, Some((0, 0))),
        Err(Error::Foreign)
    );
    assert_eq!(w.snapshot().reserved, amount());
    assert!(!w.stopped());
    assert_eq!(
        w.settle_transport(key, &current(&m), 103, id, Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
}

#[test]
fn generic_settle_cannot_bypass_bound_terminal_identity() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.cancel_transport(key, id).unwrap();
    assert_eq!(w.settle(key, &current(&m), 999, None), Err(Error::Phase));
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 101, id, None),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}

#[test]
fn missing_or_foreign_owner_scope_is_denied_before_reservation() {
    let m = manifest();
    let mut w = HarnessWave::new(Session::new(amount(), 100, 100, 20).unwrap()).unwrap();
    assert_eq!(
        w.reserve_with_transport("one", vec![worker("a", &m)], plan_one(identity()), 100)
            .unwrap_err(),
        Error::Transport(synthetic_transport::Error::Identity)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    let mut w = wave();
    let wrong = Identity {
        project: [9; 32],
        ..identity()
    };
    assert_eq!(
        w.reserve_with_transport("one", vec![worker("a", &m)], plan_one(wrong), 100)
            .unwrap_err(),
        Error::Transport(synthetic_transport::Error::Identity)
    );
    assert!(w.drained());
}

#[test]
fn missing_or_extra_transport_plan_is_denied_atomically() {
    let m = manifest();
    let mut w = wave();
    assert_eq!(
        w.reserve("one", vec![worker("a", &m)], 100).unwrap_err(),
        Error::Transport(synthetic_transport::Error::Identity)
    );
    assert!(w.drained());
    let mut w = wave();
    assert_eq!(
        w.reserve_with_transport("one", vec![worker("a", &m)], plan_pair(), 100)
            .unwrap_err(),
        Error::Transport(synthetic_transport::Error::Identity)
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    assert!(w.drained());
}

#[test]
fn duplicate_or_mismatched_peer_plan_fails_before_any_lease() {
    let m = manifest();
    for b in [
        Identity {
            worker: [4; 32],
            connection: [7; 32],
            ..identity()
        },
        Identity {
            worker: [6; 32],
            connection: [5; 32],
            ..identity()
        },
        Identity {
            attempt: [9; 32],
            worker: [6; 32],
            connection: [7; 32],
            ..identity()
        },
    ] {
        let mut w = pair_wave();
        let plan = BTreeMap::from([("a".into(), identity()), ("b".into(), b)]);
        assert_eq!(
            w.reserve_with_transport("one", vec![worker("a", &m), worker("b", &m)], plan, 100)
                .unwrap_err(),
            Error::Transport(synthetic_transport::Error::Identity)
        );
        assert_eq!(w.snapshot().reserved, Amount::default());
        assert!(w.drained());
    }
}

#[test]
fn first_observed_connection_cannot_choose_another_workers_identity() {
    let m = manifest();
    let mut w = pair_wave();
    let keys = w
        .reserve_with_transport(
            "one",
            vec![worker("a", &m), worker("b", &m)],
            plan_pair(),
            100,
        )
        .unwrap();
    let b = plan_pair()["b"];
    assert_eq!(
        w.bind_transport(&keys[0], &current(&m), b, 100),
        Err(Error::Transport(synthetic_transport::Error::Identity))
    );
    assert!(w.stopped());
    assert_eq!(w.snapshot().reserved.ram, 32);
    w.void_transport_unstarted(&keys[0], identity()).unwrap();
    w.void_transport_unstarted(&keys[1], plan_pair()["b"])
        .unwrap();
    assert!(w.drained());
}

#[test]
fn old_wave_label_cannot_be_reissued_under_new_worker_and_digest() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(id), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    w.settle_transport(key, &current(&m), 103, id, Some((0, 0)))
        .unwrap();
    let fresh = Identity {
        attempt: [8; 32],
        worker: [9; 32],
        connection: [10; 32],
        ..id
    };
    let plan = BTreeMap::from([("b".into(), fresh)]);
    assert_eq!(
        w.reserve_with_transport("one", vec![worker("b", &m)], plan, 104)
            .unwrap_err(),
        Error::DuplicateAttempt
    );
    assert_eq!(w.snapshot().reserved, Amount::default());
    assert!(w.drained());
}

#[test]
fn fresh_wave_label_and_connection_can_be_admitted_after_success() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(id), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.record_frame(key, &current(&m), 101, id, &frame(id, 1, 1))
        .unwrap();
    w.record_frame(key, &current(&m), 102, id, &frame(id, 2, 4))
        .unwrap();
    w.settle_transport(key, &current(&m), 103, id, Some((0, 0)))
        .unwrap();
    let fresh = Identity {
        attempt: [8; 32],
        connection: [9; 32],
        ..id
    };
    let keys = w
        .reserve_with_transport("two", vec![worker("a", &m)], plan_one(fresh), 104)
        .unwrap();
    w.bind_transport(&keys[0], &current(&m), fresh, 104)
        .unwrap();
    assert!(!w.stopped());
    w.stop();
    w.void_transport_unstarted(&keys[0], fresh).unwrap();
    assert!(w.drained());
}

#[test]
fn generic_or_foreign_never_started_observation_retains_planned_lease() {
    let m = manifest();
    let id = identity();
    for bound in [false, true] {
        let mut w = wave();
        let keys = w
            .reserve_with_transport("one", vec![worker("a", &m)], plan_one(id), 100)
            .unwrap();
        let key = &keys[0];
        if bound {
            w.bind_transport(key, &current(&m), id, 100).unwrap();
        }
        w.stop();
        assert_eq!(w.void_unstarted(key), Err(Error::Phase));
        let foreign = Identity {
            connection: [8; 32],
            ..id
        };
        assert_eq!(
            w.void_transport_unstarted(key, foreign),
            Err(Error::Foreign)
        );
        assert_eq!(w.snapshot().reserved, amount());
        w.void_transport_unstarted(key, id).unwrap();
        assert!(w.drained());
    }
}

#[test]
fn exact_never_started_observation_cannot_release_dispatched_worker() {
    let m = manifest();
    let id = identity();
    let mut w = wave();
    let keys = w
        .reserve_with_transport("one", vec![worker("a", &m)], plan_one(id), 100)
        .unwrap();
    let key = &keys[0];
    w.bind_transport(key, &current(&m), id, 100).unwrap();
    w.dispatch(key, &current(&m), 100).unwrap();
    w.stop();
    assert_eq!(w.void_transport_unstarted(key, id), Err(Error::Phase));
    assert_eq!(w.snapshot().reserved, amount());
    assert_eq!(
        w.settle_transport(key, &current(&m), 101, id, None),
        Ok(ResultState::NoPublication)
    );
    assert!(w.drained());
}
