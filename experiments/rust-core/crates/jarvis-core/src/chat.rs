// SPDX-License-Identifier: AGPL-3.0-only
//! Pure, bounded chat-state fixture. The trusted owner issues access handles;
//! these are not network credentials, durable IDs or an authentication system.
use crate::workspace::SessionId;
use std::collections::BTreeMap;
use std::sync::Arc;

const MAX_MESSAGES: usize = 1024;
const MAX_TEXT: usize = 16_384;
const MAX_ACCESS: u64 = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccessMode {
    Read,
    Write,
}

#[derive(Debug)]
pub struct Access {
    id: u64,
    identity: Arc<()>,
}

struct AccessRow {
    identity: Arc<()>,
    mode: AccessMode,
}

#[derive(Debug)]
pub struct Turn {
    identity: Arc<()>,
    sequence: u64,
}

struct Pending {
    identity: Arc<()>,
    sequence: u64,
    owner: u64,
    cancelled: bool,
}

#[derive(Debug)]
pub enum Submission {
    New(Turn),
    Replay(u64),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Role {
    User,
    Assistant,
}

#[derive(Debug, Eq, PartialEq)]
pub struct Message {
    pub sequence: u64,
    pub role: Role,
    pub text: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Invalid,
    Denied,
    Conflict,
    Busy,
    Limit,
    Archived,
    Foreign,
}

pub struct Chat {
    session: SessionId,
    revision: u64,
    messages: Vec<Message>,
    access: BTreeMap<u64, AccessRow>,
    issued: u64,
    requests: BTreeMap<(u64, String), u64>,
    pending: Option<Pending>,
    archived: bool,
}

impl Chat {
    /// The trusted owner must bind this to its registered session exactly once.
    pub fn new(session: SessionId) -> Self {
        Self {
            session,
            revision: 0,
            messages: Vec::new(),
            access: BTreeMap::new(),
            issued: 0,
            requests: BTreeMap::new(),
            pending: None,
            archived: false,
        }
    }

    pub fn session(&self) -> SessionId {
        self.session
    }

    pub fn grant(&mut self, mode: AccessMode) -> Result<Access, Error> {
        if self.issued >= MAX_ACCESS {
            return Err(Error::Limit);
        }
        self.issued += 1;
        let identity = Arc::new(());
        self.access.insert(
            self.issued,
            AccessRow {
                identity: identity.clone(),
                mode,
            },
        );
        Ok(Access {
            id: self.issued,
            identity,
        })
    }

    fn check(&self, handle: &Access, write: bool) -> Result<(), Error> {
        let row = self.access.get(&handle.id).ok_or(Error::Denied)?;
        if !Arc::ptr_eq(&row.identity, &handle.identity) || (write && row.mode != AccessMode::Write)
        {
            return Err(Error::Denied);
        }
        Ok(())
    }

    pub fn read(&self, handle: &Access) -> Result<(u64, &[Message]), Error> {
        self.check(handle, false)?;
        Ok((self.revision, &self.messages))
    }

    pub fn revoke(&mut self, handle: &Access) -> Result<(), Error> {
        self.check(handle, false)?;
        self.access.remove(&handle.id);
        if let Some(pending) = self.pending.as_mut() {
            if pending.owner == handle.id && !pending.cancelled {
                pending.cancelled = true;
                self.revision += 1;
            }
        }
        Ok(())
    }

    fn valid_text(text: &str) -> bool {
        !text.trim().is_empty() && text.len() <= MAX_TEXT && !text.contains('\0')
    }

    fn valid_key(key: &str) -> bool {
        !key.is_empty()
            && key.len() <= 128
            && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
    }

    /// A retry of the exact previously accepted request returns its sequence,
    /// not a second execution ticket, even after completion or cancellation.
    pub fn submit(
        &mut self,
        handle: &Access,
        expected: u64,
        key: &str,
        text: &str,
    ) -> Result<Submission, Error> {
        self.check(handle, true)?;
        if !Self::valid_key(key) || !Self::valid_text(text) {
            return Err(Error::Invalid);
        }
        let request = (handle.id, key.to_owned());
        if let Some(&sequence) = self.requests.get(&request) {
            let old = &self.messages[sequence as usize - 1];
            if old.text != text {
                return Err(Error::Conflict);
            }
            return Ok(Submission::Replay(sequence));
        }
        if self.archived {
            return Err(Error::Archived);
        }
        if expected != self.revision {
            return Err(Error::Conflict);
        }
        if self.pending.is_some() {
            return Err(Error::Busy);
        }
        // Reserve space for both the user message and a possible reply.
        if self.messages.len() + 2 > MAX_MESSAGES {
            return Err(Error::Limit);
        }
        let sequence = self.messages.len() as u64 + 1;
        let identity = Arc::new(());
        self.messages.push(Message {
            sequence,
            role: Role::User,
            text: text.to_owned(),
        });
        self.requests.insert(request, sequence);
        self.pending = Some(Pending {
            identity: identity.clone(),
            sequence,
            owner: handle.id,
            cancelled: false,
        });
        self.revision += 1;
        Ok(Submission::New(Turn { identity, sequence }))
    }

    /// Only the trusted runtime owns the turn ticket. No model effects here.
    pub fn complete(&mut self, turn: &Turn, text: &str) -> Result<(), Error> {
        let pending = self.pending.as_ref().ok_or(Error::Foreign)?;
        if turn.sequence != pending.sequence || !Arc::ptr_eq(&turn.identity, &pending.identity) {
            return Err(Error::Foreign);
        }
        if pending.cancelled {
            return Err(Error::Conflict);
        }
        if !Self::valid_text(text) {
            return Err(Error::Invalid);
        }
        self.messages.push(Message {
            sequence: self.messages.len() as u64 + 1,
            role: Role::Assistant,
            text: text.to_owned(),
        });
        self.pending = None;
        self.revision += 1;
        Ok(())
    }

    pub fn cancel(&mut self, handle: &Access, expected: u64) -> Result<(), Error> {
        self.check(handle, true)?;
        if expected != self.revision {
            return Err(Error::Conflict);
        }
        let pending = self.pending.as_mut().ok_or(Error::Conflict)?;
        if pending.cancelled {
            return Err(Error::Conflict);
        }
        pending.cancelled = true;
        self.revision += 1;
        Ok(())
    }

    /// Trusted terminal evidence for a cancelled turn, not a cancellation request.
    /// The future process broker must establish that all owned work has exited.
    pub fn observe_drained(&mut self, turn: &Turn) -> Result<(), Error> {
        let pending = self.pending.as_ref().ok_or(Error::Foreign)?;
        if turn.sequence != pending.sequence || !Arc::ptr_eq(&turn.identity, &pending.identity) {
            return Err(Error::Foreign);
        }
        if !pending.cancelled {
            return Err(Error::Conflict);
        }
        self.pending = None;
        self.revision += 1;
        Ok(())
    }

    /// Archiving is a separate owner action, not tab closure or job cancellation.
    pub fn archive(&mut self) -> Result<(), Error> {
        if self.pending.is_some() {
            return Err(Error::Busy);
        }
        if !self.archived {
            self.archived = true;
            self.revision += 1;
        }
        Ok(())
    }
}
