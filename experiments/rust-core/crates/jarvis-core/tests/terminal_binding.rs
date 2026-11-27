// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::terminal_binding::*;
use jarvis_core::workshop::{
    Agent, Client, Error, Intent, Observation, Provider, Readiness, Request, Target, Transport,
};
use jarvis_core::workspace::Workspace;

fn fixture() -> (Workspace, Attachment, TerminalObservation) {
    let mut w = Workspace::default();
    let project = w.project().unwrap();
    let session = w.session(Some(project)).unwrap();
    let target = Target {
        session,
        project: Some(project),
        agent: Agent::Manual,
        provider: Provider::None,
        transport: Transport::Tmux,
        instance: 1,
        executable_revision: 1,
        workspace_revision: 1,
        policy_epoch: 1,
    };
    let endpoint = Endpoint::Tmux {
        server: 1,
        socket: 2,
        session: 3,
        pane: 4,
        process_generation: 5,
    };
    (
        w,
        Attachment {
            request: Request {
                target,
                intent: Intent::ManualTerminal,
                client: Client::Local,
                client_instance: 1,
            },
            endpoint,
            access: Access::ManualInput,
        },
        TerminalObservation {
            agent: Observation {
                target,
                readiness: Readiness::Ready,
                private_transport: true,
                structured_no_host_tools: false,
                local_broker_only: false,
            },
            endpoint,
        },
    )
}

#[test]
fn exact_private_manual_attachment_and_view_are_distinct() {
    let (w, mut a, mut o) = fixture();
    assert_eq!(
        evaluate(&w, a, o, Some(a)),
        Ok(TerminalRoute::LocalManualInput)
    );
    let old = a;
    a.access = Access::View;
    a.request.intent = Intent::ObserveStatus;
    o.agent.readiness = Readiness::Busy;
    assert_eq!(evaluate(&w, a, o, Some(old)), Err(Error::Denied));
    assert_eq!(
        evaluate(&w, a, o, Some(a)),
        Ok(TerminalRoute::LocalReadOnlyView)
    );
    o.agent.readiness = Readiness::Draining;
    assert_eq!(
        evaluate(&w, a, o, Some(a)),
        Ok(TerminalRoute::LocalReadOnlyView)
    );
}

#[test]
fn every_tmux_identity_component_is_bound() {
    let (w, a, mut o) = fixture();
    for changed in 0..5 {
        let mut ids = [1, 2, 3, 4, 5];
        ids[changed] += 10;
        o.endpoint = Endpoint::Tmux {
            server: ids[0],
            socket: ids[1],
            session: ids[2],
            pane: ids[3],
            process_generation: ids[4],
        };
        assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::Drift));
    }
}

#[test]
fn remote_and_nonprivate_terminal_views_are_refused() {
    let (w, mut a, mut o) = fixture();
    a.access = Access::View;
    a.request.intent = Intent::ObserveStatus;
    a.request.client = Client::Remote;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::UnsafeRoute));
    a.request.client = Client::Local;
    o.agent.private_transport = false;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::UnsafeRoute));
}

#[test]
fn workspace_registry_rejects_foreign_project_and_session() {
    let (w, mut a, mut o) = fixture();
    let mut other = Workspace::default();
    a.request.target.project = Some(other.project().unwrap());
    o.agent.target = a.request.target;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::Drift));
    a.request.target.session = other.session(a.request.target.project).unwrap();
    o.agent.target = a.request.target;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::Drift));
}

#[test]
fn pty_attachment_requires_its_own_exact_endpoint() {
    let (w, mut a, mut o) = fixture();
    a.request.target.transport = Transport::Pty;
    o.agent.target = a.request.target;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::Invalid));
    a.endpoint = Endpoint::Pty {
        endpoint: 7,
        process_generation: 8,
    };
    o.endpoint = a.endpoint;
    assert_eq!(
        evaluate(&w, a, o, Some(a)),
        Ok(TerminalRoute::LocalManualInput)
    );
    o.endpoint = Endpoint::Pty {
        endpoint: 7,
        process_generation: 9,
    };
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::Drift));
}

#[test]
fn busy_or_cloud_agent_cannot_become_manual_input() {
    let (w, mut a, mut o) = fixture();
    o.agent.readiness = Readiness::Busy;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::NotReady));
    o.agent.readiness = Readiness::Ready;
    a.request.target.agent = Agent::Codex;
    a.request.target.provider = Provider::Cloud;
    o.agent.target = a.request.target;
    assert_eq!(evaluate(&w, a, o, Some(a)), Err(Error::UnsafeRoute));
}
