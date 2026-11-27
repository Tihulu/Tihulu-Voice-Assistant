// SPDX-License-Identifier: AGPL-3.0-only
//! Restricted UTF-8 LF framing for future ACP stdio JSON-RPC decoding.
//! No JSON validation, I/O, logging, dispatch, or authorization.
//! One instance per trusted connection. EOF never proves process exit.
pub const MAX_LINE: usize = 65_536;
pub const MAX_CHUNK: usize = 65_536;
pub const MAX_LINES: usize = 16;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Closed,
    Chunk,
    Line,
    Count,
    Empty,
    Control,
    Utf8,
    Partial,
}

#[derive(Default)]
pub struct Decoder {
    pending: Vec<u8>,
    closed: bool,
}

impl Decoder {
    pub fn closed(&self) -> bool {
        self.closed
    }

    fn fail<T>(&mut self, error: Error) -> Result<T, Error> {
        self.pending.clear();
        self.closed = true;
        Err(error)
    }

    /// All-or-nothing per supplied chunk: no completed line escapes if a
    /// later byte in the same chunk is invalid. Incomplete UTF-8 is held
    /// until LF, but bounded by MAX_LINE. No implicit reconnection.
    pub fn push(&mut self, chunk: &[u8]) -> Result<Vec<String>, Error> {
        if self.closed {
            return Err(Error::Closed);
        }
        if chunk.len() > MAX_CHUNK {
            return self.fail(Error::Chunk);
        }
        let mut lines = Vec::new();
        for &byte in chunk {
            if byte == b'\n' {
                if self.pending.iter().all(u8::is_ascii_whitespace) {
                    return self.fail(Error::Empty);
                }
                if lines.len() >= MAX_LINES {
                    return self.fail(Error::Count);
                }
                let bytes = std::mem::take(&mut self.pending);
                let Ok(line) = String::from_utf8(bytes) else {
                    return self.fail(Error::Utf8);
                };
                lines.push(line);
            } else {
                // Restricted LF-only profile, tabs allowed as JSON whitespace.
                // Other raw ASCII controls, including CR and DEL, are denied.
                if (byte < 0x20 && byte != b'\t') || byte == 0x7f {
                    return self.fail(Error::Control);
                }
                if self.pending.len() == MAX_LINE {
                    return self.fail(Error::Line);
                }
                self.pending.push(byte);
            }
        }
        Ok(lines)
    }

    /// Closing a byte stream is only a transport observation.
    pub fn finish(&mut self) -> Result<(), Error> {
        if self.closed {
            return Err(Error::Closed);
        }
        if !self.pending.is_empty() {
            return self.fail(Error::Partial);
        }
        self.closed = true;
        Ok(())
    }
}
