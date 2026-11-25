# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Reading local folders the owner pointed at, bounded and behind the policy.

A folder is chosen once by the owner and stored; listing it is bounded in
entries and a preview is bounded in characters. Nothing is written, nothing is
followed out of the chosen root, and a binary file is refused before it is
decoded rather than shown as noise.
"""
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .voice.m1 import Hold

MAX_ENTRIES = 200
MAX_PREVIEW = 8000
MAX_ROOT = 4096
MAX_SCAN = 1024
MAX_BYTES = 262144
MAX_QUERY = 128
MAX_MATCHES = 30
MAX_SNIPPET = 240
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    directory: bool
    identity: tuple = ()

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
    identity: tuple = ()
    limited: bool = False

    def checked(self):
        root = self.root
        if not isinstance(root, str) or not root or len(root) > MAX_ROOT:
            raise Hold("invalid_local_settings")
        try:
            path = Path(root)
        except (TypeError, ValueError):
            raise Hold("invalid_local_settings") from None
        if not path.is_absolute():
            raise Hold("folder_not_found")
        for entry in self.entries:
            entry.checked()
        return self


def checked_root(root):
    """A stored folder: absolute, existing, and bounded in length."""
    if not isinstance(root, str) or not root or len(root) > MAX_ROOT:
        raise Hold("invalid_local_settings")
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts or "\x00" in root:
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


def _open_root(root, checkpoint):
    """Open each component without following links, keeping the directory pinned."""
    root = checked_root(root)
    fd = None
    try:
        checkpoint()
        fd = os.open("/", _DIR_FLAGS)
        for part in Path(root).parts[1:]:
            checkpoint()
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        checkpoint()
        result, fd = fd, None
        return result
    except (OSError, ValueError):
        raise Hold("folder_not_found") from None
    finally:
        if fd is not None:
            os.close(fd)


def _root_identity(info):
    return info.st_dev, info.st_ino


def root_identity(root, *, checkpoint=lambda: None):
    fd = _open_root(root, checkpoint)
    try:
        checkpoint()
        return _root_identity(os.fstat(fd))
    except OSError:
        raise Hold("folder_not_found") from None
    finally:
        os.close(fd)


def require_root_identity(root, expected, *, checkpoint=lambda: None):
    if root_identity(root, checkpoint=checkpoint) != expected:
        raise Hold("folder_changed")
    checkpoint()


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _valid_name(name):
    if not isinstance(name, str) or not name or len(name) > 255 or name in (".", ".."):
        return False
    if any(c in name for c in ("/", "\\", "\x00")) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return False
    try:
        name.encode("utf-8")
    except UnicodeError:
        return False
    return True


def list_dir(root, *, checkpoint=lambda: None, expected_root=None):
    """Scan bounded metadata without following symlinks or opening file contents."""
    root = checked_root(root)
    fd = _open_root(root, checkpoint)
    try:
        identity = _root_identity(os.fstat(fd))
        if expected_root is not None and identity != expected_root:
            raise Hold("folder_changed")
        entries = []
        limited = False
        with os.scandir(fd) as rows:
            for index, row in enumerate(rows):
                checkpoint()
                if index >= MAX_SCAN:
                    limited = True
                    break
                if not _valid_name(row.name):
                    continue
                try:
                    info = row.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
                    entries.append(Entry(row.name, info.st_size, stat.S_ISDIR(info.st_mode),
                                         _file_identity(info)))
        entries.sort(key=lambda entry: (not entry.directory, entry.name.lower(), entry.name))
        checkpoint()
        if expected_root is not None:
            require_root_identity(root, expected_root, checkpoint=checkpoint)
        return Folder(root, tuple(entries[:MAX_ENTRIES]), identity,
                      limited or len(entries) > MAX_ENTRIES).checked()
    except OSError:
        raise Hold("folder_not_found") from None
    finally:
        os.close(fd)


def _read_text(root, name, *, expected_root=None, expected_entry=None,
               checkpoint=lambda: None):
    """Read only the pinned regular entry, never a link, device or FIFO."""
    if not _valid_name(name):
        raise Hold("invalid_local_settings")
    folder = _open_root(root, checkpoint)
    fd = None
    try:
        if expected_root is not None and _root_identity(os.fstat(folder)) != expected_root:
            raise Hold("folder_changed")
        checkpoint()
        try:
            info = os.stat(name, dir_fd=folder, follow_symlinks=False)
        except OSError:
            raise Hold("file_not_found") from None
        if not stat.S_ISREG(info.st_mode):
            raise Hold("file_not_found")
        before = _file_identity(info)
        if expected_entry is not None and before != expected_entry:
            raise Hold("file_changed")
        if info.st_size > MAX_BYTES:
            raise Hold("file_too_large")
        checkpoint()
        # On Linux, pin the inode with O_PATH first: opening a replaced
        # device node for reading could have side effects before fstat.
        if not hasattr(os, "O_PATH"):
            raise Hold("safe_file_open_unavailable")
        pinned = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
        try:
            info = os.fstat(pinned)
            if not stat.S_ISREG(info.st_mode) or _file_identity(info) != before:
                raise Hold("file_changed")
            checkpoint()
            fd = os.open("/proc/self/fd/%d" % pinned, _FILE_FLAGS & ~os.O_NOFOLLOW)
        finally:
            os.close(pinned)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != before:
            raise Hold("file_changed")
        chunks = bytearray()
        while len(chunks) <= MAX_BYTES:
            checkpoint()
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > MAX_BYTES:
            raise Hold("file_too_large")
        if _file_identity(os.fstat(fd)) != before or len(chunks) != opened.st_size:
            raise Hold("file_changed")
        checkpoint()
    except OSError:
        raise Hold("file_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)
        os.close(folder)
    data = bytes(chunks)
    if b"\x00" in data:
        raise Hold("file_not_text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Hold("file_not_text") from None
    if not text.strip():
        raise Hold("file_empty")
    checkpoint()
    return text


def preview(root, name, limit=MAX_PREVIEW, *, expected_root=None,
            expected_entry=None, checkpoint=lambda: None):
    if type(limit) is not int or not 1 <= limit <= MAX_PREVIEW:
        raise Hold("invalid_local_settings")
    text = _read_text(root, name, expected_root=expected_root,
                      expected_entry=expected_entry, checkpoint=checkpoint).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " …"
    checkpoint()
    return text


@dataclass(frozen=True)
class Match:
    line: int
    snippet: str


@dataclass(frozen=True)
class TextSearch:
    matches: tuple
    matching_lines: int


def checked_query(query):
    if (not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in query)):
        raise Hold("invalid_file_query")
    return query.strip()


def search_text(root, name, query, *, expected_root=None, expected_entry=None,
                checkpoint=lambda: None):
    """Literal search of one selected bounded snapshot, not a directory crawl."""
    query = checked_query(query)
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    text = _read_text(root, name, expected_root=expected_root,
                      expected_entry=expected_entry, checkpoint=checkpoint)
    matches = []
    total = 0
    for number, line in enumerate(text.splitlines(), 1):
        if number % 128 == 1:
            checkpoint()
        hit = pattern.search(line)
        if hit is None:
            continue
        total += 1
        if len(matches) < MAX_MATCHES:
            start = max(0, hit.start() - 40)
            end = min(len(line), start + MAX_SNIPPET)
            snippet = ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")
            matches.append(Match(number, snippet))
    checkpoint()
    return TextSearch(tuple(matches), total)
