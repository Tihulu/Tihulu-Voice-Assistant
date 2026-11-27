// SPDX-License-Identifier: AGPL-3.0-only
//! Exact terminal attachment contract. All observations are trusted fixture
//! inputs, not real socket, process, file or authentication evidence.
use crate::workshop::{self, Client, Error, Intent, Observation, Request, Transport};
use crate::workspace::Workspace;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Endpoint {
    Pty {
        endpoint: u64,
        process_generation: u64,
    },
    Tmux {
        server: u64,
        socket: u64,
        session: u64,
        pane: u64,
        process_generation: u64,
    },
}

impl Endpoint {
    fn valid(self, transport: Transport) -> bool {
        match (self, transport) {
            (
                Self::Pty {
                    endpoint,
                    process_generation,
                },
                Transport::Pty,
            ) => endpoint != 0 && process_generation != 0,
            (
                Self::Tmux {
                    server,
                    socket,
                    session,
                    pane,
                    process_generation,
                },
                Transport::Tmux,
            ) => [server, socket, session, pane, process_generation]
                .into_iter()
                .all(|id| id != 0),
            _ => false,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Access {
    View,
    ManualInput,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Attachment {
    pub request: Request,
    pub endpoint: Endpoint,
    pub access: Access,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TerminalObservation {
    pub agent: Observation,
    pub endpoint: Endpoint,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TerminalRoute {
    LocalReadOnlyView,
    LocalManualInput,
}

/// Only a decision for the common owner to consume. No opening sockets,
/// attaching terminal devices, escaping a sandbox or granting execution.
pub fn evaluate(
    workspace: &Workspace,
    attachment: Attachment,
    observation: TerminalObservation,
    approved: Option<Attachment>,
) -> Result<TerminalRoute, Error> {
    let request = attachment.request;
    let target = request.target;
    if !attachment.endpoint.valid(target.transport) {
        return Err(Error::Invalid);
    }
    if workspace.session_project(target.session) != Ok(target.project)
        || observation.endpoint != attachment.endpoint
        || observation.agent.target != target
    {
        return Err(Error::Drift);
    }
    if approved != Some(attachment) {
        return Err(Error::Denied);
    }
    if request.client != Client::Local
        || target.project.is_none()
        || !observation.agent.private_transport
    {
        return Err(Error::UnsafeRoute);
    }
    match attachment.access {
        Access::View if request.intent == Intent::ObserveStatus => {
            workshop::evaluate(request, observation.agent, Some(request))?;
            Ok(TerminalRoute::LocalReadOnlyView)
        }
        Access::ManualInput if request.intent == Intent::ManualTerminal => {
            workshop::evaluate(request, observation.agent, Some(request))?;
            Ok(TerminalRoute::LocalManualInput)
        }
        _ => Err(Error::UnsafeRoute),
    }
}
