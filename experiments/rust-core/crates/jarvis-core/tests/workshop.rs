// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::workshop::*;
use jarvis_core::workspace::Workspace;

fn fixture() -> (Request, Observation) {
    let mut w = Workspace::default();
    let project = w.project().unwrap();
    let session = w.session(Some(project)).unwrap();
    let target = Target {
        session,
        project: Some(project),
        agent: Agent::JanQwen,
        provider: Provider::Local,
        transport: Transport::Structured,
        instance: 1,
        executable_revision: 1,
        workspace_revision: 1,
        policy_epoch: 1,
    };
    (
        Request {
            target,
            intent: Intent::HostTask,
            client: Client::Local,
            client_instance: 1,
        },
        Observation {
            target,
            readiness: Readiness::Ready,
            private_transport: true,
            structured_no_host_tools: true,
            local_broker_only: true,
        },
    )
}

#[test]
fn local_host_task_requires_exact_approval_and_broker() {
    let (r, mut o) = fixture();
    assert_eq!(evaluate(r, o, Some(r)), Ok(Route::LocalBroker));
    assert_eq!(evaluate(r, o, None), Err(Error::Denied));
    o.local_broker_only = false;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
}

#[test]
fn every_external_agent_and_cloud_host_route_is_refused() {
    for agent in [
        Agent::OpenCode,
        Agent::Pi,
        Agent::ClaudeCode,
        Agent::Codex,
        Agent::DeepSeekHarness,
        Agent::Manual,
    ] {
        let (mut r, mut o) = fixture();
        r.target.agent = agent;
        o.target = r.target;
        assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
    }
    let (mut r, mut o) = fixture();
    r.target.provider = Provider::Cloud;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
}

#[test]
fn cloud_plan_requires_structured_no_host_tools() {
    let (mut r, mut o) = fixture();
    r.intent = Intent::Plan;
    r.target.agent = Agent::Codex;
    r.target.provider = Provider::Cloud;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Ok(Route::StructuredPlan));
    o.structured_no_host_tools = false;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
    o.structured_no_host_tools = true;
    r.target.transport = Transport::Tmux;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
}

#[test]
fn readiness_is_not_inferred_from_installation_or_open_terminal() {
    let (r, mut o) = fixture();
    for state in [
        Readiness::Discovered,
        Readiness::Installed,
        Readiness::Compatible,
        Readiness::Reviewed,
        Readiness::Busy,
        Readiness::Draining,
        Readiness::Blocked,
        Readiness::Closed,
    ] {
        o.readiness = state;
        assert_eq!(evaluate(r, o, Some(r)), Err(Error::NotReady));
    }
}

#[test]
fn exact_target_drift_is_rejected() {
    let (r, o) = fixture();
    let mut changed = o;
    changed.target.instance += 1;
    assert_eq!(evaluate(r, changed, Some(r)), Err(Error::Drift));
    changed = o;
    changed.target.executable_revision += 1;
    assert_eq!(evaluate(r, changed, Some(r)), Err(Error::Drift));
    changed = o;
    changed.target.workspace_revision += 1;
    assert_eq!(evaluate(r, changed, Some(r)), Err(Error::Drift));
    changed = o;
    changed.target.policy_epoch += 1;
    assert_eq!(evaluate(r, changed, Some(r)), Err(Error::Drift));
    changed = o;
    changed.target.project = None;
    assert_eq!(evaluate(r, changed, Some(r)), Err(Error::Drift));
}

#[test]
fn approved_intent_and_client_cannot_be_reused_for_another_action() {
    let (r, o) = fixture();
    let mut changed = r;
    changed.intent = Intent::ObserveStatus;
    assert_eq!(evaluate(changed, o, Some(r)), Err(Error::Denied));
    changed = r;
    changed.client = Client::Remote;
    assert_eq!(evaluate(changed, o, Some(r)), Err(Error::Denied));
    changed = r;
    changed.client_instance += 1;
    assert_eq!(evaluate(changed, o, Some(r)), Err(Error::Denied));
}

#[test]
fn remote_status_is_sanitized_and_not_execution() {
    let (mut r, mut o) = fixture();
    r.client = Client::Remote;
    r.intent = Intent::ObserveStatus;
    for state in [Readiness::Ready, Readiness::Busy, Readiness::Draining] {
        o.readiness = state;
        assert_eq!(evaluate(r, o, Some(r)), Ok(Route::SanitizedStatus));
    }
    r.intent = Intent::HostTask;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::NotReady));
}

#[test]
fn manual_terminal_is_local_private_and_not_an_agent_route() {
    let (mut r, mut o) = fixture();
    r.intent = Intent::ManualTerminal;
    r.target.agent = Agent::Manual;
    r.target.provider = Provider::None;
    r.target.transport = Transport::Tmux;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Ok(Route::LocalManualTerminal));
    o.private_transport = false;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
    o.private_transport = true;
    r.client = Client::Remote;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
    r.client = Client::Local;
    r.target.agent = Agent::ClaudeCode;
    r.target.provider = Provider::Cloud;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
}

#[test]
fn general_chat_cannot_gain_host_project_scope() {
    let (mut r, mut o) = fixture();
    r.target.project = None;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::UnsafeRoute));
}

#[test]
fn zero_identity_is_invalid_even_with_matching_approval() {
    let (mut r, mut o) = fixture();
    r.target.instance = 0;
    o.target = r.target;
    assert_eq!(evaluate(r, o, Some(r)), Err(Error::Invalid));
}
