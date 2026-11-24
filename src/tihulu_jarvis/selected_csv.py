# SPDX-License-Identifier: AGPL-3.0-only
"""Read a selected CSV exactly once, for the trusted local controller.

Linux-specific descriptor-relative reader. This is NOT a model-facing tool:
the path must come directly from a user selection and the authorization callback
must be owned by the controller, not the model. No permission is granted here.
"""
import os
import stat
from dataclasses import dataclass

from .temperature_engine import MAX_BYTES
from .voice.m1 import Hold

CHUNK = 16384


@dataclass(frozen=True)
class SelectedCSV:
    data: bytes


def _check(authorized, cancelled):
    if cancelled():
        raise Hold("analysis_cancelled")
    if authorized() is not True:
        raise Hold("csv_selection_not_authorized")


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def read_selected_csv(path, *, authorized, cancelled=lambda: False):
    """Return a bounded immutable snapshot; never send a path to the model.

    All path components are opened without following symlinks. The selected
    pathname is resolved at open time; once open, all reads and checks concern
    the same descriptor. Reject detectable mutation, short reads and oversize
    files. This does not prove the inode was unchanged BEFORE the open; a
    stronger picker may supply an already-open descriptor in a future adapter.
    """
    _check(authorized, cancelled)
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise Hold("csv_invalid_selection")
    parts = path.split("/")[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise Hold("csv_invalid_selection")
    if not all(hasattr(os, flag) for flag in
               ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC")):
        raise Hold("csv_secure_reader_unavailable")
    if os.open not in os.supports_dir_fd:
        raise Hold("csv_secure_reader_unavailable")
    directory = None
    fd = None
    try:
        base_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open("/", base_flags | os.O_DIRECTORY)
        for part in parts[:-1]:
            _check(authorized, cancelled)
            child = os.open(part, base_flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        _check(authorized, cancelled)
        # NONBLOCK prevents a malicious FIFO from waiting for a writer.
        fd = os.open(parts[-1], base_flags | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise Hold("csv_not_regular_file")
        if before.st_size <= 0 or before.st_size > MAX_BYTES:
            raise Hold("csv_invalid_size")
        data = bytearray()
        while True:
            _check(authorized, cancelled)
            chunk = os.read(fd, min(CHUNK, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise Hold("csv_invalid_size")
        after = os.fstat(fd)
        if _identity(before) != _identity(after) or len(data) != before.st_size:
            raise Hold("csv_changed_during_read")
        _check(authorized, cancelled)
        return SelectedCSV(bytes(data))
    except OSError:
        # Do not propagate private paths or raw system diagnostics.
        raise Hold("csv_selection_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)
