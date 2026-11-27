// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::acp_v1::{Adapter, Error, Phase, StopReason, Update};
use jarvis_core::budget::{Amount, Demand};
use jarvis_core::harness::{self, Capability, Event, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::{Current, HarnessWave, PolicyStamp, ResultState, Worker};
use jarvis_core::session::Session;
use jarvis_core::synthetic_transport::{decode, Identity};
use std::collections::{BTreeMap, BTreeSet};

fn manifest() -> Manifest {
    Manifest {
        kind: Kind::OpenCode,
        protocol: Protocol::AcpV1,
        pin: [1; 32],
        supported: BTreeSet::from([Capability::Chat, Capability::Stream, Capability::Cancel]),
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

fn id() -> Identity {
    Identity {
        project: [1; 32],
        session: [2; 32],
        attempt: [3; 32],
        worker: [4; 32],
        connection: [5; 32],
    }
}

fn adapter(m: &Manifest) -> Adapter {
    Adapter::new(id(), m, &current(m), &m.supported).unwrap()
}

fn ready(m: &Manifest) -> Adapter {
    let mut a = adapter(m);
    a.initialized(id(), &current(m), 0, 1, false).unwrap();
    a.begin_session(id(), &current(m)).unwrap();
    a.session_created(id(), &current(m), 1, "sess_1").unwrap();
    a
}

#[test]
fn exact_handshake_required_and_foreign_reply_does_not_mutate() {
    let m = manifest();
    let mut a = adapter(&m);
    let wrong = Identity {
        connection: [9; 32],
        ..id()
    };
    assert_eq!(
        a.initialized(wrong, &current(&m), 0, 1, false),
        Err(Error::Foreign)
    );
    assert_eq!(a.phase(), Phase::Initializing);
    a.initialized(id(), &current(&m), 0, 1, false).unwrap();
    a.begin_session(id(), &current(&m)).unwrap();
    a.session_created(id(), &current(&m), 1, "sess_1").unwrap();
    assert_eq!(a.phase(), Phase::Ready);
}

#[test]
fn wrong_version_id_auth_and_out_of_order_fail_closed() {
    let m = manifest();
    for (rpc, version, auth, error) in [
        (1, 1, false, Error::RpcId),
        (0, 2, false, Error::Version),
        (0, 0, false, Error::Version),
        (0, 1, true, Error::Authentication),
    ] {
        let mut a = adapter(&m);
        assert_eq!(
            a.initialized(id(), &current(&m), rpc, version, auth),
            Err(error)
        );
        assert_eq!(a.phase(), Phase::Closed);
        assert!(a.initialized(id(), &current(&m), 0, 1, false).is_err());
    }
    let mut a = adapter(&m);
    assert_eq!(a.begin_prompt(id(), &current(&m)), Err(Error::Phase));
}

#[test]
fn v2_and_unsupported_capabilities_not_admitted() {
    let mut m = manifest();
    m.protocol = Protocol::AcpV2;
    assert!(matches!(
        Adapter::new(id(), &m, &current(&m), &m.supported),
        Err(Error::Version)
    ));
    m.protocol = Protocol::AcpV1;
    m.supported.insert(Capability::History);
    assert!(matches!(
        Adapter::new(id(), &m, &current(&m), &m.supported),
        Err(Error::Unsupported)
    ));
}

#[test]
fn session_id_limits_and_scope_drift() {
    let m = manifest();
    for bad in ["", "bad\n", "/path", &"x".repeat(257)] {
        let mut a = adapter(&m);
        a.initialized(id(), &current(&m), 0, 1, false).unwrap();
        a.begin_session(id(), &current(&m)).unwrap();
        assert_eq!(
            a.session_created(id(), &current(&m), 1, bad),
            Err(Error::Session)
        );
    }
    let mut a = ready(&m);
    a.begin_prompt(id(), &current(&m)).unwrap();
    assert_eq!(
        a.update(id(), &current(&m), "other", Update::Plan),
        Err(Error::Session)
    );
}

#[test]
fn policy_or_manifest_drift_closes_before_event() {
    let m = manifest();
    let mut a = ready(&m);
    let mut changed = current(&m);
    changed.policy.generation = 2;
    assert_eq!(a.begin_prompt(id(), &changed), Err(Error::Policy));
    let mut a = ready(&m);
    let mut other = m.clone();
    other.pin = [9; 32];
    assert!(matches!(
        a.begin_prompt(id(), &current(&other)),
        Err(Error::Harness(_))
    ));
}

#[test]
fn permission_holds_and_never_reports_success() {
    let m = manifest();
    let mut a = ready(&m);
    a.begin_prompt(id(), &current(&m)).unwrap();
    let frame = a
        .permission_requested(id(), &current(&m), "sess_1")
        .unwrap();
    assert_eq!(frame.event, Event::ApprovalNeeded);
    assert_eq!(a.phase(), Phase::Held);
    assert!(a
        .prompt_result(id(), &current(&m), 2, StopReason::EndTurn)
        .is_err());
}

#[test]
fn unsupported_tools_and_oversized_updates_fail_closed() {
    let m = manifest();
    for update in [
        Update::ToolCall,
        Update::ToolCallUpdate,
        Update::Unsupported,
        Update::Text { bytes: 16_385 },
    ] {
        let mut a = ready(&m);
        a.begin_prompt(id(), &current(&m)).unwrap();
        assert!(a.update(id(), &current(&m), "sess_1", update).is_err());
        assert_eq!(a.phase(), Phase::Closed);
    }
}

#[test]
fn only_end_turn_reports_success_and_no_duplicate_terminal() {
    let m = manifest();
    for reason in [
        StopReason::EndTurn,
        StopReason::MaxTokens,
        StopReason::MaxTurnRequests,
        StopReason::Refusal,
        StopReason::Cancelled,
        StopReason::Unknown,
    ] {
        let mut a = ready(&m);
        a.begin_prompt(id(), &current(&m)).unwrap();
        let frame = a.prompt_result(id(), &current(&m), 2, reason).unwrap();
        assert_eq!(
            frame.event,
            if reason == StopReason::EndTurn {
                Event::ReportedDone
            } else {
                Event::ReportedFailure
            }
        );
        assert!(a.prompt_result(id(), &current(&m), 2, reason).is_err());
    }
}

#[test]
fn typed_events_flow_through_existing_wave_and_trusted_settle() {
    let m = manifest();
    let capacity = Amount {
        ram: 16,
        vram: 8,
        generated: 16,
        prompt: 112,
    };
    let worker = Worker {
        demand: Demand {
            worker: "a".into(),
            amount: capacity,
        },
        plan: harness::negotiate(&m, &m, &m.supported, &m.supported, true).unwrap(),
        current: current(&m),
    };
    let mut w = HarnessWave::new_scoped(
        Session::new(capacity, 100, 100, 20).unwrap(),
        id().project,
        id().session,
    )
    .unwrap();
    let keys = w
        .reserve_with_transport(
            "one",
            vec![worker],
            BTreeMap::from([("a".into(), id())]),
            100,
        )
        .unwrap();
    let mut a = ready(&m);
    w.bind_transport(&keys[0], &current(&m), id(), 100).unwrap();
    w.dispatch(&keys[0], &current(&m), 100).unwrap();
    let frames = [
        a.begin_prompt(id(), &current(&m)).unwrap(),
        a.update(id(), &current(&m), "sess_1", Update::Text { bytes: 5 })
            .unwrap(),
        a.prompt_result(id(), &current(&m), 2, StopReason::EndTurn)
            .unwrap(),
    ];
    for (i, frame) in frames.into_iter().enumerate() {
        let bytes = frame.encode().unwrap();
        assert_eq!(decode(&bytes), Ok(frame));
        w.record_frame(&keys[0], &current(&m), 101 + i as u64, id(), &bytes)
            .unwrap();
    }
    assert_eq!(w.snapshot().reserved, capacity);
    assert_eq!(
        w.settle_transport(&keys[0], &current(&m), 104, id(), Some((0, 0))),
        Ok(ResultState::MeasuredReport)
    );
    assert!(w.idle());
}
