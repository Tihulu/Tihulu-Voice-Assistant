// SPDX-License-Identifier: AGPL-3.0-only
//! Pure harness admission and event mapping fixture. No process, IO, provider,
//! authorization issuance or budget effects. The existing owner must supply
//! reviewed manifests, current observations and policy scope.
use std::collections::BTreeSet;
use std::sync::Arc;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Kind {
    NativeJanQwen,
    OpenCode,
    Pi,
    Codex,
    Claude,
    DeepSeek,
    TerminalObserver,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Protocol {
    NativeV1,
    OpenCodeV1,
    AcpV1,
    AcpV2,
    PiRpcV1,
    CodexV1,
    ClaudeV1,
    DeepSeekTextV1,
    PtyObserverV1,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Route {
    Local,
    CloudPlan,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Capability {
    Chat,
    Stream,
    History,
    Cancel,
    Propose,
    IsolatedWork,
    HostShell,
    ObserveTerminal,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Manifest {
    pub kind: Kind,
    pub protocol: Protocol,
    pub pin: [u8; 32],
    pub supported: BTreeSet<Capability>,
    pub route: Route,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    InvalidManifest,
    Drift,
    Denied,
    Foreign,
    Sequence,
    State,
}

fn compatible(m: &Manifest) -> bool {
    use Kind::*;
    use Protocol::*;
    matches!(
        (m.kind, m.protocol),
        (NativeJanQwen, NativeV1)
            | (OpenCode, OpenCodeV1 | AcpV1 | AcpV2)
            | (Pi, PiRpcV1 | AcpV1 | AcpV2)
            | (Codex, CodexV1 | AcpV1 | AcpV2)
            | (Claude, ClaudeV1 | AcpV1 | AcpV2)
            | (DeepSeek, DeepSeekTextV1)
            | (TerminalObserver, PtyObserverV1)
    ) && m.pin != [0; 32]
        && !m.supported.is_empty()
        && (!matches!(m.kind, NativeJanQwen | TerminalObserver) || m.route == Route::Local)
        && m.supported.iter().all(|c| match m.kind {
            TerminalObserver => *c == Capability::ObserveTerminal,
            DeepSeek => !matches!(
                c,
                Capability::HostShell | Capability::IsolatedWork | Capability::ObserveTerminal
            ),
            _ => {
                *c != Capability::HostShell || (m.kind == NativeJanQwen && m.route == Route::Local)
            }
        })
        && (m.route == Route::Local
            || m.supported.iter().all(|c| {
                matches!(
                    c,
                    Capability::Chat
                        | Capability::Stream
                        | Capability::History
                        | Capability::Cancel
                        | Capability::Propose
                )
            }))
}

/// A negotiated descriptor is not a permission grant or dispatch ticket.
#[derive(Debug)]
pub struct Negotiated {
    manifest: Manifest,
    requested: BTreeSet<Capability>,
}

pub fn negotiate(
    reviewed: &Manifest,
    observed: &Manifest,
    allowed: &BTreeSet<Capability>,
    requested: &BTreeSet<Capability>,
    local_only: bool,
) -> Result<Negotiated, Error> {
    if !compatible(reviewed) {
        return Err(Error::InvalidManifest);
    }
    if reviewed != observed {
        return Err(Error::Drift);
    }
    if requested.is_empty()
        || !requested.is_subset(&reviewed.supported)
        || !requested.is_subset(allowed)
        || (local_only && reviewed.route != Route::Local)
    {
        return Err(Error::Denied);
    }
    Ok(Negotiated {
        manifest: reviewed.clone(),
        requested: requested.clone(),
    })
}

impl Negotiated {
    /// Recheck current trusted observations and policy before dispatch.
    /// No external call is made here.
    pub fn recheck(
        &self,
        observed: &Manifest,
        allowed: &BTreeSet<Capability>,
        local_only: bool,
    ) -> Result<(), Error> {
        negotiate(
            &self.manifest,
            observed,
            allowed,
            &self.requested,
            local_only,
        )?;
        Ok(())
    }
}

#[derive(Debug)]
pub struct EventKey(Arc<()>);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Event {
    Started,
    Progress,
    ApprovalNeeded,
    ReportedDone,
    ReportedFailure,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Phase {
    AwaitingStart,
    Running,
    Held,
    Draining,
    Drained,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Outcome {
    ReportedSuccessAndDrained,
    NoPublication,
}

pub struct EventTrack {
    identity: Arc<()>,
    sequence: u64,
    phase: Phase,
    success: bool,
}

impl EventTrack {
    /// Trusted owner binds this track to its existing attempt; it is not an
    /// alternate scheduler. No authority to launch or publish is created.
    pub fn new() -> (Self, EventKey) {
        let identity = Arc::new(());
        (
            Self {
                identity: identity.clone(),
                sequence: 0,
                phase: Phase::AwaitingStart,
                success: false,
            },
            EventKey(identity),
        )
    }

    pub fn phase(&self) -> Phase {
        self.phase
    }

    fn exact(&self, key: &EventKey) -> Result<(), Error> {
        if Arc::ptr_eq(&self.identity, &key.0) {
            Ok(())
        } else {
            Err(Error::Foreign)
        }
    }

    pub fn cancel(&mut self) {
        if self.phase != Phase::Drained {
            self.phase = Phase::Draining;
            self.success = false;
        }
    }

    pub fn record(&mut self, key: &EventKey, sequence: u64, event: Event) -> Result<(), Error> {
        self.exact(key)?;
        if matches!(self.phase, Phase::Draining | Phase::Drained) {
            return Err(Error::State);
        }
        if self.sequence.checked_add(1) != Some(sequence) {
            self.cancel();
            return Err(Error::Sequence);
        }
        match (self.phase, event) {
            (Phase::AwaitingStart, Event::Started) => self.phase = Phase::Running,
            (Phase::Running, Event::Progress) => {}
            (Phase::Running, Event::ApprovalNeeded) => self.phase = Phase::Held,
            (Phase::Running, Event::ReportedDone) => {
                self.phase = Phase::Draining;
                self.success = true;
            }
            (Phase::Running, Event::ReportedFailure) => self.cancel(),
            _ => {
                self.cancel();
                return Err(Error::State);
            }
        }
        self.sequence = sequence;
        Ok(())
    }

    /// Only a separately verified owner observation may call this. A protocol
    /// completion report by itself is never evidence of process exit.
    /// Result is diagnostic, not permission to publish content.
    pub fn observe_drained(&mut self, key: &EventKey) -> Result<Outcome, Error> {
        self.exact(key)?;
        if self.phase != Phase::Draining {
            return Err(Error::State);
        }
        self.phase = Phase::Drained;
        Ok(if self.success {
            Outcome::ReportedSuccessAndDrained
        } else {
            Outcome::NoPublication
        })
    }
}
