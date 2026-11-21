# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Export a conversation the owner had, as a bounded local file.

An export is a rendering of what the session holds: messages with their speaker
and time, and nothing else. No endpoint, no model path, no profile rule and no
policy decision is written, because none of those are part of what was said.
The write is atomic and the file is private by mode.
"""
import os
import tempfile
from pathlib import Path

from .voice.m1 import Hold

MAX_BYTES = 1048576
MAX_MESSAGES = 500


def render(history, started=None):
    """The conversation as bounded text, oldest first.

    A message that is not a (role, content) pair is skipped rather than guessed
    at, the total is bounded, and a cut is stated at the end rather than hidden.
    """
    lines = []
    if started is not None:
        lines.append("# TIHULU JARVIS conversation exported locally")
        lines.append("# fields: speaker, time, text. Nothing else is recorded.")
    total = 0
    for entry in history[:MAX_MESSAGES]:
        role = getattr(entry, "role", None)
        content = getattr(entry, "content", None)
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        line = "%s: %s" % (role, content.replace("\r\n", " ").replace("\n", " "))
        total += len(line) + 1
        if total > MAX_BYTES:
            lines.append("# truncated: the conversation is longer than %d bytes" % MAX_BYTES)
            return "\n".join(lines) + "\n"
        lines.append(line)
    return "\n".join(lines) + "\n"


def write(path, text):
    """Write one export atomically, privately, or refuse with a reason."""
    if not isinstance(path, str) or not path:
        raise Hold("invalid_local_settings")
    target = Path(path)
    if not target.is_absolute():
        raise Hold("invalid_local_settings")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise Hold("export_too_large")
    parent = target.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tihulu-export-", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    return str(target)
