// SPDX-License-Identifier: AGPL-3.0-only
//! Restricted ACP v1 typed lifecycle fixture, based on official initialization,
//! session setup and prompt-turn docs. NOT a JSON-RPC parser or live adapter.
//! No client fs/terminal/auth/MCP capability, session load, tools or IO.
use crate::harness::{self, Capability, Event, Manifest, Negotiated, Protocol};
use crate::harness_wave::{Current, PolicyStamp};
use crate::synthetic_transport::{Frame, Identity};
use std::collections::BTreeSet;

pub const INITIALIZE_ID: u64 = 0;
pub const SESSION_NEW_ID: u64 = 1;
pub const PROMPT_ID: u64 = 2;
pub const MAX_SESSION_ID: usize = 256;
pub const MAX_UPDATE_BYTES: usize = 16_384;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Phase {
    Initializing,
    Initialized,
    Creating,
    Ready,
    Prompting,
    Held,
    Terminal,
    Closed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Foreign,
    Phase,
    Version,
    Authentication,
    RpcId,
    Session,
    Unsupported,
    Limit,
    Policy,
    Harness(harness::Error),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StopReason {
    EndTurn,
    MaxTokens,
    MaxTurnRequests,
    Refusal,
    Cancelled,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Update {
    Text { bytes: usize },
    Plan,
    ToolCall,
    ToolCallUpdate,
    Unsupported,
}

/// Already-decoded, bounded fixture observations. No raw content retained.
pub struct Adapter {
    identity: Identity,
    plan: Negotiated,
    policy: PolicyStamp,
    phase: Phase,
    session: Option<String>,
    sequence: u64,
}

impl Adapter {
    pub fn new(
        identity: Identity,
        reviewed: &Manifest,
        current: &Current<'_>,
        requested: &BTreeSet<Capability>,
    ) -> Result<Self, Error> {
        if !identity.valid() {
            return Err(Error::Foreign);
        }
        if reviewed.protocol != Protocol::AcpV1 {
            return Err(Error::Version);
        }
        if current.policy.generation == 0 {
            return Err(Error::Policy);
        }
        if !requested.contains(&Capability::Chat)
            || requested.iter().any(|c| {
                !matches!(
                    c,
                    Capability::Chat
                        | Capability::Stream
                        | Capability::Cancel
                        | Capability::Propose
                )
            })
        {
            return Err(Error::Unsupported);
        }
        let plan = harness::negotiate(
            reviewed,
            current.manifest,
            current.allowed,
            requested,
            current.policy.local_only,
        )
        .map_err(Error::Harness)?;
        Ok(Self {
            identity,
            plan,
            policy: current.policy,
            phase: Phase::Initializing,
            session: None,
            sequence: 0,
        })
    }

    pub fn phase(&self) -> Phase {
        self.phase
    }

    fn fail<T>(&mut self, error: Error) -> Result<T, Error> {
        self.phase = Phase::Closed;
        Err(error)
    }

    fn check(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        expected: Phase,
    ) -> Result<(), Error> {
        // Foreign connection cannot mutate this adapter's state.
        if source != self.identity {
            return Err(Error::Foreign);
        }
        if self.phase != expected {
            return self.fail(Error::Phase);
        }
        if self.policy != current.policy {
            return self.fail(Error::Policy);
        }
        if let Err(error) =
            self.plan
                .recheck(current.manifest, current.allowed, current.policy.local_only)
        {
            return self.fail(Error::Harness(error));
        }
        Ok(())
    }

    /// The client offered v1 only and no optional client capabilities.
    /// Conservatively HOLD any authentication-method negotiation.
    pub fn initialized(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        rpc_id: u64,
        protocol_version: u16,
        offers_authentication: bool,
    ) -> Result<(), Error> {
        self.check(source, current, Phase::Initializing)?;
        if rpc_id != INITIALIZE_ID {
            return self.fail(Error::RpcId);
        }
        if protocol_version != 1 {
            return self.fail(Error::Version);
        }
        if offers_authentication {
            return self.fail(Error::Authentication);
        }
        self.phase = Phase::Initialized;
        Ok(())
    }

    /// Owner has separately reviewed the working directory. No path or MCP
    /// descriptor is accepted here and no session/new message is transmitted.
    pub fn begin_session(&mut self, source: Identity, current: &Current<'_>) -> Result<(), Error> {
        self.check(source, current, Phase::Initialized)?;
        self.phase = Phase::Creating;
        Ok(())
    }

    pub fn session_created(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        rpc_id: u64,
        session_id: &str,
    ) -> Result<(), Error> {
        self.check(source, current, Phase::Creating)?;
        if rpc_id != SESSION_NEW_ID {
            return self.fail(Error::RpcId);
        }
        // Deliberately restricted opaque ASCII subset; no paths or controls.
        if session_id.is_empty()
            || session_id.len() > MAX_SESSION_ID
            || !session_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            return self.fail(Error::Session);
        }
        self.session = Some(session_id.to_owned());
        self.phase = Phase::Ready;
        Ok(())
    }

    fn frame(&mut self, event: Event) -> Result<Frame, Error> {
        let Some(next) = self.sequence.checked_add(1) else {
            return self.fail(Error::Limit);
        };
        self.sequence = next;
        Ok(Frame {
            protocol: Protocol::AcpV1,
            identity: self.identity,
            sequence: next,
            event,
        })
    }

    fn prompt_scope(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        session_id: &str,
    ) -> Result<(), Error> {
        self.check(source, current, Phase::Prompting)?;
        if self.session.as_deref() != Some(session_id) {
            return self.fail(Error::Session);
        }
        Ok(())
    }

    /// Trusted owner observation of prompt dispatch, not a dispatch ticket.
    pub fn begin_prompt(
        &mut self,
        source: Identity,
        current: &Current<'_>,
    ) -> Result<Frame, Error> {
        self.check(source, current, Phase::Ready)?;
        self.phase = Phase::Prompting;
        self.frame(Event::Started)
    }

    pub fn update(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        session_id: &str,
        update: Update,
    ) -> Result<Frame, Error> {
        self.prompt_scope(source, current, session_id)?;
        match update {
            Update::Text { bytes } if bytes <= MAX_UPDATE_BYTES => self.frame(Event::Progress),
            Update::Plan => self.frame(Event::Progress),
            Update::Text { .. } => self.fail(Error::Limit),
            // No tool execution is supported in this restricted pilot.
            Update::ToolCall | Update::ToolCallUpdate | Update::Unsupported => {
                self.fail(Error::Unsupported)
            }
        }
    }

    /// ACP permission request is a hold, never a grant. Caller must cancel
    /// the actual RPC separately in a future reviewed wire adapter.
    pub fn permission_requested(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        session_id: &str,
    ) -> Result<Frame, Error> {
        self.prompt_scope(source, current, session_id)?;
        self.phase = Phase::Held;
        self.frame(Event::ApprovalNeeded)
    }

    /// A prompt result is not a process-exit observation.
    pub fn prompt_result(
        &mut self,
        source: Identity,
        current: &Current<'_>,
        rpc_id: u64,
        reason: StopReason,
    ) -> Result<Frame, Error> {
        self.check(source, current, Phase::Prompting)?;
        if rpc_id != PROMPT_ID {
            return self.fail(Error::RpcId);
        }
        self.phase = Phase::Terminal;
        self.frame(if reason == StopReason::EndTurn {
            Event::ReportedDone
        } else {
            Event::ReportedFailure
        })
    }

    pub fn close(&mut self) {
        self.phase = Phase::Closed;
    }
}
