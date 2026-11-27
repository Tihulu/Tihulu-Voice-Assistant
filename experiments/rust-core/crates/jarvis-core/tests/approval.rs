// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::approval::{Binding, Error, Gate, Phase};
use jarvis_core::harness_wave::PolicyStamp;

fn binding() -> Binding {
    Binding {
        subject: [1; 32],
        action: [2; 32],
        policy: PolicyStamp {
            generation: 1,
            local_only: true,
        },
        expires_at: 120,
    }
}

#[test]
fn exact_approval_is_single_use() {
    let b = binding();
    let mut gate = Gate::new(b, 100).unwrap();
    let receipt = gate.approve(&b, 101).unwrap();
    assert_eq!(gate.phase(), Phase::Approved);
    assert_eq!(gate.approve(&b, 101).unwrap_err(), Error::State);
    gate.consume(&receipt, &b, 102).unwrap();
    assert_eq!(gate.phase(), Phase::Consumed);
    assert_eq!(gate.consume(&receipt, &b, 103), Err(Error::State));
    gate.revoke();
    assert_eq!(gate.phase(), Phase::Consumed);
}

#[test]
fn foreign_receipt_cannot_mutate_gate_or_clock() {
    let b = binding();
    let mut first = Gate::new(b, 100).unwrap();
    let mut second = Gate::new(b, 100).unwrap();
    let foreign = first.approve(&b, 101).unwrap();
    let exact = second.approve(&b, 101).unwrap();
    assert_eq!(second.consume(&foreign, &b, 999), Err(Error::Foreign));
    second.consume(&exact, &b, 102).unwrap();
}

#[test]
fn changed_action_subject_or_policy_closes_approval() {
    let b = binding();
    let changes = [
        Binding {
            subject: [3; 32],
            ..b
        },
        Binding {
            action: [3; 32],
            ..b
        },
        Binding {
            policy: PolicyStamp {
                generation: 2,
                ..b.policy
            },
            ..b
        },
        Binding {
            policy: PolicyStamp {
                local_only: false,
                ..b.policy
            },
            ..b
        },
        Binding {
            expires_at: 121,
            ..b
        },
    ];
    for changed in changes {
        let mut gate = Gate::new(b, 100).unwrap();
        let receipt = gate.approve(&b, 101).unwrap();
        assert_eq!(gate.consume(&receipt, &changed, 102), Err(Error::Drift));
        assert_eq!(gate.phase(), Phase::Closed);
        assert_eq!(gate.consume(&receipt, &b, 103), Err(Error::State));
    }
}

#[test]
fn wrong_binding_at_decision_cannot_be_retried() {
    let b = binding();
    let mut gate = Gate::new(b, 100).unwrap();
    let changed = Binding {
        action: [9; 32],
        ..b
    };
    assert_eq!(gate.approve(&changed, 101).unwrap_err(), Error::Drift);
    assert_eq!(gate.approve(&b, 102).unwrap_err(), Error::State);
}

#[test]
fn exact_expiry_and_clock_regression_are_terminal() {
    let b = binding();
    let mut gate = Gate::new(b, 100).unwrap();
    assert_eq!(gate.approve(&b, 120).unwrap_err(), Error::Expired);
    let mut gate = Gate::new(b, 100).unwrap();
    let receipt = gate.approve(&b, 110).unwrap();
    assert_eq!(gate.consume(&receipt, &b, 109), Err(Error::ClockRegression));
    assert_eq!(gate.phase(), Phase::Closed);
    let mut gate = Gate::new(b, 100).unwrap();
    let receipt = gate.approve(&b, 119).unwrap();
    assert_eq!(gate.consume(&receipt, &b, 120), Err(Error::Expired));
}

#[test]
fn revocation_before_or_after_decision_is_terminal() {
    let b = binding();
    let mut gate = Gate::new(b, 100).unwrap();
    gate.revoke();
    assert_eq!(gate.approve(&b, 101).unwrap_err(), Error::State);
    let mut gate = Gate::new(b, 100).unwrap();
    let receipt = gate.approve(&b, 101).unwrap();
    gate.revoke();
    assert_eq!(gate.consume(&receipt, &b, 102), Err(Error::State));
}

#[test]
fn invalid_identity_or_lifetime_is_not_admitted() {
    let b = binding();
    for invalid in [
        Binding {
            subject: [0; 32],
            ..b
        },
        Binding {
            action: [0; 32],
            ..b
        },
        Binding {
            policy: PolicyStamp {
                generation: 0,
                ..b.policy
            },
            ..b
        },
        Binding {
            expires_at: 100,
            ..b
        },
        Binding {
            expires_at: u64::MAX,
            ..b
        },
    ] {
        assert!(matches!(Gate::new(invalid, 100), Err(Error::Invalid)));
    }
}
