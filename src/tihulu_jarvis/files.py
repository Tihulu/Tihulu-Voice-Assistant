# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Reading local folders the owner pointed at, bounded and behind the policy.

A folder is chosen once by the owner and stored; listing it is bounded in
entries and a preview is bounded in characters. Nothing is written, nothing is
followed out of the chosen root, and a binary file is refused before it is
decoded rather than shown as noise.
"""
from dataclasses import dataclass
from pathlib import Path

from .voice.m1 import Hold

MAX_ENTRIES = 200
MAX_PREVIEW = 8000
MAX_ROOT = 4096


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    directory: bool

    def checked(self):
        if not self.name or len(self.name) > 255 or self.name in (".", ".."):
            raise Hold("invalid_local_settings")
        if self.size < 0:
            raise Hold("invalid_local_settings")
        return self


@dataclass(frozen=True)
class Folder:
    root: str
    entries: tuple

    def checked(self):
        root = self.root
        if not isinstance(root, str) or not root or len(root) > MAX_ROOT:
            raise Hold("invalid_local_settings")
        try:
            path = Path(root)
        except (TypeError, ValueError):
            raise Hold("invalid_local_settings") from None
        if not path.is_absolute() or not path.is_dir():
            raise Hold("folder_not_found")
        for entry in self.entries:
            entry.checked()
        return self


def checked_root(root):
    """A stored folder: absolute, existing, and bounded in length."""
    if not isinstance(root, str) or not root or len(root) > MAX_ROOT:
        raise Hold("invalid_local_settings")
    path = Path(root)
    if not path.is_absolute():
        raise Hold("invalid_local_settings")
    return str(path)


def inside(root, name):
    """One entry inside the root, or a bounded refusal.

    A name that leaves the root — by traversal or by being absolute — is
    refused before anything is opened.
    """
    root_path = Path(checked_root(root))
    if not isinstance(name, str) or not name or len(name) > 255 \
            or name in (".", "..") or "/" in name or "\\" in name:
        raise Hold("invalid_local_settings")
    target = root_path / name
    try:
        target.resolve().relative_to(root_path.resolve())
    except (ValueError, OSError):
        raise Hold("path_outside_root") from None
    return target


def list_dir(root):
    """The folder's entries, bounded and sorted, with sizes when known."""
    path = Path(checked_root(root))
    if not path.is_dir():
        raise Hold("folder_not_found")
    entries = []
    for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            entries.append(Entry(item.name, item.stat().st_size, item.is_dir()))
        except OSError:
            # An entry we cannot state is skipped, never guessed at.
            continue
        if len(entries) >= MAX_ENTRIES:
            break
    return Folder(str(path), tuple(entries)).checked()


def preview(root, name, limit=MAX_PREVIEW):
    """Bounded text of one file inside the root, or a bounded reason."""
    target = inside(root, name)
    if not target.is_file():
        raise Hold("file_not_found")
    size = target.stat().st_size
    if size > 262144:
        raise Hold("file_too_large")
    try:
        data = target.read_bytes()
    except OSError:
        raise Hold("file_unreadable") from None
    if b"\x00" in data[:4096]:
        raise Hold("file_not_text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Hold("file_not_text") from None
    text = text.strip()
    if not text:
        raise Hold("file_empty")
    if len(text) > limit:
        return text[:limit].rstrip() + " …"
    return text
