// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::harness::*;
use std::collections::BTreeSet;

fn caps(values: &[Capability]) -> BTreeSet<Capability> {
    values.iter().copied().collect()
}

fn manifest(kind: Kind, protocol: Protocol, route: Route, values: &[Capability]) -> Manifest {
    Manifest {
        kind,
        protocol,
        pin: [1; 32],
        supported: caps(values),
        route,
    }
}

fn accepted(m: &Manifest, requested: &[Capability]) -> Result<Negotiated, Error> {
    negotiate(m, m, &m.supported, &caps(requested), false)
}

#[test]
fn exact_reviewed_protocol_and_pin_required() {
    let m = manifest(
        Kind::OpenCode,
        Protocol::AcpV1,
        Route::Local,
        &[Capability::Chat, Capability::Cancel],
    );
    let requested = caps(&[Capability::Chat]);
    let plan = accepted(&m, &[Capability::Chat]).unwrap();
    for observed in [
        Manifest {
            protocol: Protocol::AcpV2,
            ..m.clone()
        },
        Manifest {
            pin: [2; 32],
            ..m.clone()
        },
        Manifest {
            supported: caps(&[Capability::Chat]),
            ..m.clone()
        },
    ] {
        assert_eq!(
            negotiate(&m, &observed, &m.supported, &requested, false).unwrap_err(),
            Error::Drift
        );
        assert_eq!(
            plan.recheck(&observed, &m.supported, false),
            Err(Error::Drift)
        );
    }
    assert_eq!(plan.recheck(&m, &caps(&[]), false), Err(Error::Denied));
}

#[test]
fn discovery_is_not_permission() {
    let m = manifest(
        Kind::Pi,
        Protocol::PiRpcV1,
        Route::Local,
        &[Capability::Chat, Capability::IsolatedWork],
    );
    assert_eq!(
        negotiate(
            &m,
            &m,
            &caps(&[Capability::Chat]),
            &caps(&[Capability::IsolatedWork]),
            false
        )
        .unwrap_err(),
        Error::Denied
    );
    assert_eq!(accepted(&m, &[]).unwrap_err(), Error::Denied);
    assert_eq!(
        accepted(&m, &[Capability::History]).unwrap_err(),
        Error::Denied
    );
}

#[test]
fn cloud_only_plans_and_never_passes_local_only() {
    let m = manifest(
        Kind::Codex,
        Protocol::CodexV1,
        Route::CloudPlan,
        &[Capability::Chat, Capability::Propose],
    );
    let plan = accepted(&m, &[Capability::Propose]).unwrap();
    assert_eq!(plan.recheck(&m, &m.supported, true), Err(Error::Denied));
    let unsafe_m = Manifest {
        supported: caps(&[Capability::Chat, Capability::IsolatedWork]),
        ..m
    };
    assert_eq!(
        accepted(&unsafe_m, &[Capability::Chat]).unwrap_err(),
        Error::InvalidManifest
    );
}

#[test]
fn host_shell_only_native_local_jan_qwen() {
    for (kind, protocol) in [
        (Kind::OpenCode, Protocol::OpenCodeV1),
        (Kind::Claude, Protocol::ClaudeV1),
        (Kind::Pi, Protocol::PiRpcV1),
        (Kind::NativeJanQwen, Protocol::NativeV1),
    ] {
        let m = manifest(kind, protocol, Route::Local, &[Capability::HostShell]);
        if kind == Kind::NativeJanQwen {
            assert!(accepted(&m, &[Capability::HostShell]).is_ok());
        } else {
            assert_eq!(
                accepted(&m, &[Capability::HostShell]).unwrap_err(),
                Error::InvalidManifest
            );
        }
    }
    let m = manifest(
        Kind::NativeJanQwen,
        Protocol::NativeV1,
        Route::CloudPlan,
        &[Capability::Chat],
    );
    assert_eq!(
        accepted(&m, &[Capability::Chat]).unwrap_err(),
        Error::InvalidManifest
    );
}

#[test]
fn terminal_observer_and_deepseek_text_cannot_expand_scope() {
    for (kind, protocol, allowed) in [
        (
            Kind::TerminalObserver,
            Protocol::PtyObserverV1,
            Capability::ObserveTerminal,
        ),
        (Kind::DeepSeek, Protocol::DeepSeekTextV1, Capability::Chat),
    ] {
        let m = manifest(kind, protocol, Route::Local, &[allowed]);
        assert!(accepted(&m, &[allowed]).is_ok());
        let expanded = manifest(
            kind,
            protocol,
            Route::Local,
            &[allowed, Capability::IsolatedWork],
        );
        assert_eq!(
            accepted(&expanded, &[allowed]).unwrap_err(),
            Error::InvalidManifest
        );
    }
}

#[test]
fn incompatible_protocol_and_zero_pin_refused() {
    let m = manifest(
        Kind::Pi,
        Protocol::CodexV1,
        Route::Local,
        &[Capability::Chat],
    );
    assert_eq!(
        accepted(&m, &[Capability::Chat]).unwrap_err(),
        Error::InvalidManifest
    );
    let m = Manifest {
        protocol: Protocol::PiRpcV1,
        pin: [0; 32],
        ..m
    };
    assert_eq!(
        accepted(&m, &[Capability::Chat]).unwrap_err(),
        Error::InvalidManifest
    );
}

#[test]
fn completion_report_is_not_drain() {
    let (mut track, key) = EventTrack::new();
    track.record(&key, 1, Event::Started).unwrap();
    assert_eq!(track.observe_drained(&key), Err(Error::State));
    track.record(&key, 2, Event::Progress).unwrap();
    track.record(&key, 3, Event::ReportedDone).unwrap();
    assert_eq!(track.phase(), Phase::Draining);
    assert_eq!(
        track.observe_drained(&key),
        Ok(Outcome::ReportedSuccessAndDrained)
    );
    assert_eq!(track.observe_drained(&key), Err(Error::State));
}

#[test]
fn foreign_event_cannot_mutate_track() {
    let (mut a, key) = EventTrack::new();
    let (_, foreign) = EventTrack::new();
    assert_eq!(a.record(&foreign, 999, Event::Started), Err(Error::Foreign));
    assert_eq!(a.phase(), Phase::AwaitingStart);
    a.record(&key, 1, Event::Started).unwrap();
    a.cancel();
    assert_eq!(a.observe_drained(&foreign), Err(Error::Foreign));
    assert_eq!(a.observe_drained(&key), Ok(Outcome::NoPublication));
}

#[test]
fn gaps_and_duplicates_fail_closed() {
    for sequence in [1, 3, u64::MAX] {
        let (mut track, key) = EventTrack::new();
        track.record(&key, 1, Event::Started).unwrap();
        assert_eq!(
            track.record(&key, sequence, Event::Progress),
            Err(Error::Sequence)
        );
        assert_eq!(track.phase(), Phase::Draining);
        assert_eq!(
            track.record(&key, 2, Event::ReportedDone),
            Err(Error::State)
        );
        assert_eq!(track.observe_drained(&key), Ok(Outcome::NoPublication));
    }
}

#[test]
fn approval_request_does_not_auto_resume() {
    let (mut track, key) = EventTrack::new();
    track.record(&key, 1, Event::Started).unwrap();
    track.record(&key, 2, Event::ApprovalNeeded).unwrap();
    assert_eq!(track.phase(), Phase::Held);
    assert_eq!(
        track.record(&key, 3, Event::ReportedDone),
        Err(Error::State)
    );
    assert_eq!(track.observe_drained(&key), Ok(Outcome::NoPublication));
}

#[test]
fn cancellation_after_report_and_before_drain_suppresses_success() {
    let (mut track, key) = EventTrack::new();
    track.record(&key, 1, Event::Started).unwrap();
    track.record(&key, 2, Event::ReportedDone).unwrap();
    track.cancel();
    assert_eq!(track.observe_drained(&key), Ok(Outcome::NoPublication));
}

#[test]
fn failure_and_start_order_violation_require_drain() {
    let (mut track, key) = EventTrack::new();
    assert_eq!(
        track.record(&key, 1, Event::ReportedDone),
        Err(Error::State)
    );
    assert_eq!(track.observe_drained(&key), Ok(Outcome::NoPublication));
    let (mut track, key) = EventTrack::new();
    track.record(&key, 1, Event::Started).unwrap();
    track.record(&key, 2, Event::ReportedFailure).unwrap();
    assert_eq!(track.observe_drained(&key), Ok(Outcome::NoPublication));
}
