// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::approval::{Binding, Error, Phase, Registry, RegistryError};
use jarvis_core::harness_wave::PolicyStamp;

fn policy() -> PolicyStamp {
    PolicyStamp {
        generation: 1,
        local_only: true,
    }
}

fn binding() -> Binding {
    Binding {
        subject: [1; 32],
        action: [2; 32],
        policy: policy(),
        expires_at: 120,
    }
}

#[test]
fn consumed_operation_cannot_be_reissued_under_new_request_or_expiry() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    let receipt = r.approve(&key, &b, policy(), 101).unwrap();
    r.consume(&key, &receipt, &b, policy(), 102).unwrap();
    let changed = Binding {
        expires_at: 125,
        ..b
    };
    assert_eq!(
        r.register([4; 32], changed, policy(), 103).unwrap_err(),
        RegistryError::Duplicate
    );
    assert_eq!(r.phase(&key), Ok(Phase::Consumed));
}

#[test]
fn request_id_cannot_be_reused_for_different_action() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    r.revoke(&key).unwrap();
    let changed = Binding {
        action: [4; 32],
        ..b
    };
    assert_eq!(
        r.register([3; 32], changed, policy(), 101).unwrap_err(),
        RegistryError::Duplicate
    );
}

#[test]
fn generation_change_revokes_all_and_preserves_history() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    let receipt = r.approve(&key, &b, policy(), 101).unwrap();
    let next = PolicyStamp {
        generation: 2,
        ..policy()
    };
    r.observe(next, 102).unwrap();
    assert_eq!(r.phase(&key), Ok(Phase::Closed));
    assert_eq!(
        r.consume(&key, &receipt, &b, next, 103),
        Err(RegistryError::Gate(Error::State))
    );
    let changed = Binding { policy: next, ..b };
    assert_eq!(
        r.register([4; 32], changed, next, 104).unwrap_err(),
        RegistryError::Duplicate
    );
    let fresh = Binding {
        action: [5; 32],
        ..changed
    };
    assert!(r.register([5; 32], fresh, next, 105).is_ok());
}

#[test]
fn foreign_keys_and_receipts_do_not_advance_owner_clock() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let mut other = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    let foreign = other.register([3; 32], b, policy(), 100).unwrap();
    let other_receipt = other.approve(&foreign, &b, policy(), 101).unwrap();
    assert_eq!(
        r.approve(&foreign, &b, policy(), 999).unwrap_err(),
        RegistryError::Foreign
    );
    let receipt = r.approve(&key, &b, policy(), 101).unwrap();
    assert_eq!(
        r.consume(&key, &other_receipt, &b, policy(), 999),
        Err(RegistryError::Foreign)
    );
    r.consume(&key, &receipt, &b, policy(), 102).unwrap();
}

#[test]
fn global_clock_regression_closes_all_pending_decisions() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    let receipt = r.approve(&key, &b, policy(), 110).unwrap();
    assert_eq!(
        r.consume(&key, &receipt, &b, policy(), 109),
        Err(RegistryError::Gate(Error::ClockRegression))
    );
    assert!(r.closed());
    assert_eq!(r.phase(&key), Ok(Phase::Closed));
}

#[test]
fn expiration_and_denial_cannot_be_reopened() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    r.observe(policy(), 120).unwrap();
    assert_eq!(r.phase(&key), Ok(Phase::Closed));
    let changed = Binding {
        expires_at: 140,
        ..b
    };
    assert_eq!(
        r.register([4; 32], changed, policy(), 121).unwrap_err(),
        RegistryError::Duplicate
    );
}

#[test]
fn bounded_history_never_evicts_tombstones() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    for i in 1..=1024_u32 {
        let mut id = [0; 32];
        id[..4].copy_from_slice(&i.to_le_bytes());
        let unique = Binding { action: id, ..b };
        let key = r.register(id, unique, policy(), 100).unwrap();
        r.revoke(&key).unwrap();
    }
    let fresh = Binding {
        action: [9; 32],
        ..b
    };
    assert_eq!(
        r.register([9; 32], fresh, policy(), 101).unwrap_err(),
        RegistryError::Limit
    );
    let mut first = [0; 32];
    first[0] = 1;
    let repeated = Binding { action: first, ..b };
    assert_eq!(
        r.register([8; 32], repeated, policy(), 101).unwrap_err(),
        RegistryError::Duplicate
    );
}

#[test]
fn contradictory_policy_closes_registry_and_all_approvals() {
    let b = binding();
    let mut r = Registry::new(policy(), 100).unwrap();
    let key = r.register([3; 32], b, policy(), 100).unwrap();
    let changed = PolicyStamp {
        local_only: false,
        ..policy()
    };
    assert_eq!(r.observe(changed, 101), Err(RegistryError::PolicyDrift));
    assert!(r.closed());
    assert_eq!(r.phase(&key), Ok(Phase::Closed));
}
