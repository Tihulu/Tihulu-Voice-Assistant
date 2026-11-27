// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::approval::{Binding, Phase, RegistryError};
use jarvis_core::approval_broker::{Action, Broker, Error, Observation, Request, Scope};
use jarvis_core::harness::{self, Capability, Kind, Manifest, Protocol, Route};
use jarvis_core::harness_wave::PolicyStamp;
use std::collections::BTreeSet;

fn policy() -> PolicyStamp {
    PolicyStamp {
        generation: 1,
        local_only: false,
    }
}

fn request() -> Request {
    Request {
        scope: Scope {
            project: [1; 32],
            session: [2; 32],
            turn: [3; 32],
            attempt: [4; 32],
            worker: [5; 32],
        },
        action: Action::HostTask {
            target: [6; 32],
            input: [7; 32],
        },
        binding: Binding {
            subject: [8; 32],
            action: [9; 32],
            policy: policy(),
            expires_at: 120,
        },
        manifest: Manifest {
            kind: Kind::NativeJanQwen,
            protocol: Protocol::NativeV1,
            pin: [10; 32],
            supported: BTreeSet::from([Capability::HostShell, Capability::Propose]),
            route: Route::Local,
        },
    }
}

fn observed(r: &Request) -> Observation<'_> {
    Observation {
        request: r,
        allowed: &r.manifest.supported,
        policy: r.binding.policy,
    }
}

fn broker() -> Broker {
    Broker::new([1; 32], [2; 32], policy(), 100).unwrap()
}

#[test]
fn exact_typed_action_consumes_once() {
    let r = request();
    let mut b = broker();
    let key = b.register([11; 32], &r, &observed(&r), 100).unwrap();
    let receipt = b.approve(&key, &observed(&r), 101).unwrap();
    b.consume(&key, &receipt, &observed(&r), 102).unwrap();
    assert_eq!(b.phase(&key), Ok(Phase::Consumed));
    assert!(b.consume(&key, &receipt, &observed(&r), 103).is_err());
}

#[test]
fn foreign_project_session_and_empty_worker_are_denied() {
    let mut b = broker();
    for i in 0..3 {
        let mut r = request();
        match i {
            0 => r.scope.project = [12; 32],
            1 => r.scope.session = [12; 32],
            _ => r.scope.worker = [0; 32],
        }
        assert_eq!(
            b.register([11; 32], &r, &observed(&r), 100).unwrap_err(),
            Error::Scope
        );
    }
}

#[test]
fn cloud_host_task_is_denied_even_with_explicit_approval_path() {
    let mut r = request();
    r.manifest = Manifest {
        kind: Kind::Codex,
        protocol: Protocol::CodexV1,
        pin: [10; 32],
        supported: BTreeSet::from([Capability::Propose]),
        route: Route::CloudPlan,
    };
    let mut b = broker();
    assert_eq!(
        b.register([11; 32], &r, &observed(&r), 100).unwrap_err(),
        Error::Harness(harness::Error::Denied)
    );
    r.action = Action::Plan {
        target: [6; 32],
        input: [7; 32],
    };
    assert!(b.register([11; 32], &r, &observed(&r), 101).is_ok());
}

#[test]
fn changed_typed_action_cannot_reuse_unchanged_digest() {
    let r = request();
    let mut b = broker();
    let key = b.register([11; 32], &r, &observed(&r), 100).unwrap();
    let receipt = b.approve(&key, &observed(&r), 101).unwrap();
    let mut changed = r.clone();
    changed.action = Action::HostTask {
        target: [12; 32],
        input: [7; 32],
    };
    assert_eq!(
        b.consume(&key, &receipt, &observed(&changed), 102),
        Err(Error::Drift)
    );
    assert_eq!(b.phase(&key), Ok(Phase::Closed));
}

#[test]
fn new_digests_cannot_reissue_same_typed_operation() {
    let r = request();
    let mut b = broker();
    let key = b.register([11; 32], &r, &observed(&r), 100).unwrap();
    let receipt = b.approve(&key, &observed(&r), 101).unwrap();
    b.consume(&key, &receipt, &observed(&r), 102).unwrap();
    let mut changed = r.clone();
    changed.binding.subject = [12; 32];
    changed.binding.action = [13; 32];
    assert_eq!(
        b.register([14; 32], &changed, &observed(&changed), 103)
            .unwrap_err(),
        Error::Duplicate
    );
}

#[test]
fn foreign_receipt_cannot_revoke_exact_pending_effect() {
    let r = request();
    let mut b = broker();
    let mut other = broker();
    let key = b.register([11; 32], &r, &observed(&r), 100).unwrap();
    let foreign = other.register([11; 32], &r, &observed(&r), 100).unwrap();
    let receipt = b.approve(&key, &observed(&r), 101).unwrap();
    let wrong = other.approve(&foreign, &observed(&r), 101).unwrap();
    let mut changed = r.clone();
    changed.scope.worker = [12; 32];
    assert_eq!(
        b.consume(&key, &wrong, &observed(&changed), 999),
        Err(Error::Registry(RegistryError::Foreign))
    );
    b.consume(&key, &receipt, &observed(&r), 102).unwrap();
}
