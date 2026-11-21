# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Attachments: local text the owner chose to send, bounded and disclosed.

An attachment is read once, bounded twice — by size on disk and by characters
in the message — and identified to the model by its *name* only. The directory
it came from never enters the transcript, and no file is opened again after the
read.
"""
from dataclasses import dataclass
from pathlib import Path

from .voice.m1 import Hold

MAX_BYTES = 262144
MAX_CHARS = 60000
ACTION = "filesystem_read"
SCOPE = ("attachment",)


@dataclass(frozen=True)
class Attachment:
    name: str
    size: int
    chars: int
    truncated: bool
    text: str

    def checked(self):
        if not self.name or len(self.name) > 120:
            raise Hold("attachment_not_text")
        if self.size < 0 or self.chars < 0 or not self.text:
            raise Hold("attachment_not_text")
        return self


def load(path):
    """Read a text attachment, bounded and identified by name only."""
    if not isinstance(path, str) or not path:
        raise Hold("attachment_not_found")
    raw = Path(path)
    if not raw.is_absolute() or not raw.is_file():
        raise Hold("attachment_not_found")
    size = raw.stat().st_size
    if size > MAX_BYTES:
        raise Hold("attachment_too_large")
    try:
        data = raw.read_bytes()
    except OSError:
        raise Hold("attachment_unreadable") from None
    if b"\x00" in data[:MAX_BYTES]:
        # A binary file is refused before it is decoded, not after.
        raise Hold("attachment_not_text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Hold("attachment_not_text") from None
    stripped = text.strip()
    if not stripped or not any(ch.isalnum() for ch in stripped):
        raise Hold("attachment_empty")
    truncated = len(stripped) > MAX_CHARS
    if truncated:
        stripped = stripped[:MAX_CHARS].rstrip()
    return Attachment(raw.name, size, len(stripped), truncated, stripped).checked()


def describe(attachment):
    """One short line for the composer: name, length and truncation."""
    line = "%s · %d characters" % (attachment.name, attachment.chars)
    if attachment.truncated:
        line += " · truncated"
    return line


def prompt_block(attachment):
    """The attachment as a bounded, clearly delimited block in the message.

    The block states its own boundaries so the model cannot read past it, and
    it carries no path: a directory structure is not part of the conversation.
    """
    return ("[attached file: %s, %d characters%s]\n%s\n[end of attached file]"
            % (attachment.name, attachment.chars,
               ", truncated" if attachment.truncated else "",
               attachment.text))
