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
from .selected_csv import MAX_BYTES, read_selected_bytes

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


def load(path, *, authorized=lambda: True):
    """Read one bounded, pinned snapshot after trusted caller authorization.

    The default is retained for local standalone readers; it is not a model
    capability or a permission grant. The desktop supplies a scoped callback.
    """
    if not isinstance(path, str) or not path:
        raise Hold("attachment_not_found")
    raw = Path(path)
    try:
        data = read_selected_bytes(path, authorized=authorized, allow_empty=True)
    except Hold as exc:
        reason = {
            "csv_invalid_selection": "attachment_not_found",
            "csv_not_regular_file": "attachment_not_found",
            "csv_selection_unreadable": "attachment_not_found",
            "csv_invalid_size": "attachment_too_large",
            "csv_changed_during_read": "attachment_changed_during_read",
            "csv_secure_reader_unavailable": "attachment_secure_reader_unavailable",
            "csv_selection_not_authorized": "attachment_not_authorized",
        }.get(str(exc), "attachment_unreadable")
        raise Hold(reason) from None
    size = len(data)
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

    The block describes the data boundaries, not a security barrier against
    prompt injection. It carries a filename, not the source directory.
    """
    return ("[attached file: %s, %d characters%s]\n%s\n[end of attached file]"
            % (attachment.name, attachment.chars,
               ", truncated" if attachment.truncated else "",
               attachment.text))
