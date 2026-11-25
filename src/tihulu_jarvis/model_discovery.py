# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit, bounded metadata-only discovery of existing audio model candidates."""
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .voice.m1 import Hold

MAX_ENTRIES = 512
MAX_CANDIDATES = 64
MAX_DEPTH = 2
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class Candidate:
    relative: str
    kind: str
    size: int


@dataclass(frozen=True)
class Result:
    root: str
    candidates: tuple
    limited: bool


def _open_root(root, checkpoint):
    """Walk each absolute path component without following any symlink."""
    parts = Path(root).parts
    if ".." in parts:
        raise Hold("model_folder_invalid")
    fd = os.open("/", _FLAGS)
    try:
        for part in parts[1:]:
            checkpoint()
            child = os.open(part, _FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def scan(root, checkpoint=lambda: None):
    """Only the selected folder and two descendant levels; never open weights.

    Suffixes identify candidates, not usable models. No symlink following,
    content reading, hashing, registration, download, or persistence.
    """
    if not isinstance(root, str) or len(root) > 4096 or not Path(root).is_absolute():
        raise Hold("model_folder_invalid")
    # Normalize trailing separators, then open each path component without
    # following symlinks; the final-component flag alone is insufficient.
    root = str(Path(root))
    try:
        root.encode("utf-8")
        checkpoint()
        fd = _open_root(root, checkpoint)
    except (OSError, UnicodeError, ValueError):
        raise Hold("model_folder_unavailable") from None
    found = []
    visited = 0
    limited = False
    def walk(folder, prefix, depth):
        nonlocal visited, limited
        checkpoint()
        with os.scandir(folder) as rows:
            for row in rows:
                checkpoint()
                if visited >= MAX_ENTRIES or len(found) >= MAX_CANDIDATES:
                    limited = True
                    return
                visited += 1
                try:
                    row.name.encode("utf-8")
                    if any(ord(c) < 32 or ord(c) == 127 for c in row.name):
                        continue
                    info = row.stat(follow_symlinks=False)
                except (OSError, UnicodeError):
                    continue
                relative = prefix + row.name
                if stat.S_ISDIR(info.st_mode):
                    if depth >= MAX_DEPTH:
                        limited = True
                        continue
                    try:
                        checkpoint()
                        child = os.open(row.name, _FLAGS, dir_fd=folder)
                    except OSError:
                        continue
                    try:
                        walk(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                    if visited >= MAX_ENTRIES or len(found) >= MAX_CANDIDATES:
                        limited = True
                        return
                elif stat.S_ISREG(info.st_mode) and info.st_size > 0:
                    kind = {".bin": "stt", ".onnx": "tts"}.get(Path(row.name).suffix.lower())
                    if kind is not None:
                        found.append(Candidate(relative, kind, info.st_size))
    try:
        walk(fd, "", 0)
        checkpoint()
        return Result(root, tuple(sorted(found, key=lambda row: row.relative)), limited)
    except OSError:
        raise Hold("model_folder_unavailable") from None
    finally:
        os.close(fd)
