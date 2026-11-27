// SPDX-License-Identifier: AGPL-3.0-only
//! Fixed-size internal transport fixture, NOT ACP wire format or JSON-RPC.
//! The owner supplies identity and reviewed adapter observations. No IO.
use crate::harness::{Event, Protocol};

pub const FRAME_LEN: usize = 174;
type Id = [u8; 32];

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Identity {
    pub project: Id,
    pub session: Id,
    pub attempt: Id,
    pub worker: Id,
    pub connection: Id,
}

impl Identity {
    pub(crate) fn valid(self) -> bool {
        [
            self.project,
            self.session,
            self.attempt,
            self.worker,
            self.connection,
        ]
        .iter()
        .all(|id| *id != [0; 32])
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Length,
    Version,
    Identity,
    Event,
    Sequence,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Frame {
    pub protocol: Protocol,
    pub identity: Identity,
    pub sequence: u64,
    pub event: Event,
}

pub fn decode(bytes: &[u8]) -> Result<Frame, Error> {
    if bytes.len() != FRAME_LEN {
        return Err(Error::Length);
    }
    if &bytes[..4] != b"JHF1" {
        return Err(Error::Version);
    }
    let protocol = match bytes[4] {
        1 => Protocol::AcpV1,
        2 => Protocol::AcpV2,
        _ => return Err(Error::Version),
    };
    let event = match bytes[5] {
        1 => Event::Started,
        2 => Event::Progress,
        3 => Event::ApprovalNeeded,
        4 => Event::ReportedDone,
        5 => Event::ReportedFailure,
        _ => return Err(Error::Event),
    };
    let sequence = u64::from_be_bytes(bytes[6..14].try_into().map_err(|_| Error::Length)?);
    if sequence == 0 {
        return Err(Error::Sequence);
    }
    let id = |start| -> Result<Id, Error> {
        bytes[start..start + 32]
            .try_into()
            .map_err(|_| Error::Length)
    };
    let identity = Identity {
        project: id(14)?,
        session: id(46)?,
        attempt: id(78)?,
        worker: id(110)?,
        connection: id(142)?,
    };
    if !identity.valid() {
        return Err(Error::Identity);
    }
    Ok(Frame {
        protocol,
        identity,
        sequence,
        event,
    })
}
