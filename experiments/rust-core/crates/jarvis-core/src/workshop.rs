// SPDX-License-Identifier: AGPL-3.0-only
//! Pure Workshop routing contract. Trusted owner observations and approvals
//! only: not authentication, OS isolation, dispatch or process-exit evidence.
use crate::workspace::{ProjectId, SessionId};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Agent {
    JanQwen,
    OpenCode,
    Pi,
    ClaudeCode,
    Codex,
    DeepSeekHarness,
    Manual,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Provider {
    Local,
    Cloud,
    None,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Transport {
    Structured,
    Pty,
    Tmux,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Intent {
    Plan,
    HostTask,
    ObserveStatus,
    ManualTerminal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Client {
    Local,
    Remote,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Readiness {
    Discovered,
    Installed,
    Compatible,
    Reviewed,
    Ready,
    Busy,
    Draining,
    Blocked,
    Closed,
}

/// The future trusted broker resolves these opaque fixture identifiers.
/// A display name, path string or terminal pane name is never identity.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Target {
    pub session: SessionId,
    pub project: Option<ProjectId>,
    pub agent: Agent,
    pub provider: Provider,
    pub transport: Transport,
    pub instance: u64,
    pub executable_revision: u64,
    pub workspace_revision: u64,
    pub policy_epoch: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Request {
    pub target: Target,
    pub intent: Intent,
    pub client: Client,
    pub client_instance: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Observation {
    pub target: Target,
    pub readiness: Readiness,
    pub private_transport: bool,
    pub structured_no_host_tools: bool,
    pub local_broker_only: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Route {
    StructuredPlan,
    LocalBroker,
    SanitizedStatus,
    LocalManualTerminal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Invalid,
    Drift,
    Denied,
    NotReady,
    UnsafeRoute,
}

/// Non-executable decision. The trusted common owner must supply a fresh
/// exact approval and observation; no independent Workshop permission store.
/// A positive route still requires shared admission and per-effect checks.
pub fn evaluate(
    request: Request,
    observation: Observation,
    approved: Option<Request>,
) -> Result<Route, Error> {
    let target = request.target;
    if target.instance == 0
        || target.executable_revision == 0
        || target.workspace_revision == 0
        || target.policy_epoch == 0
        || request.client_instance == 0
    {
        return Err(Error::Invalid);
    }
    if observation.target != target {
        return Err(Error::Drift);
    }
    if approved != Some(request) {
        return Err(Error::Denied);
    }
    if request.intent == Intent::ObserveStatus {
        return match observation.readiness {
            Readiness::Ready | Readiness::Busy | Readiness::Draining => Ok(Route::SanitizedStatus),
            _ => Err(Error::NotReady),
        };
    }
    if observation.readiness != Readiness::Ready {
        return Err(Error::NotReady);
    }
    match request.intent {
        Intent::Plan
            if target.agent != Agent::Manual
                && target.provider != Provider::None
                && target.transport == Transport::Structured
                && observation.structured_no_host_tools =>
        {
            Ok(Route::StructuredPlan)
        }
        Intent::HostTask
            if target.agent == Agent::JanQwen
                && target.provider == Provider::Local
                && target.transport == Transport::Structured
                && target.project.is_some()
                && observation.local_broker_only =>
        {
            Ok(Route::LocalBroker)
        }
        Intent::ManualTerminal
            if request.client == Client::Local
                && target.agent == Agent::Manual
                && target.provider == Provider::None
                && target.project.is_some()
                && matches!(target.transport, Transport::Pty | Transport::Tmux)
                && observation.private_transport =>
        {
            Ok(Route::LocalManualTerminal)
        }
        _ => Err(Error::UnsafeRoute),
    }
}
