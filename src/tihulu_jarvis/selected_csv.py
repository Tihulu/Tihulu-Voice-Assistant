# SPDX-License-Identifier: AGPL-3.0-only
"""Read a selected CSV exactly once, for the trusted local controller.

Linux-specific descriptor-relative reader. This is NOT a model-facing tool:
the path must come directly from a user selection and the authorization callback
must be owned by the controller, not the model. No permission is granted here.
"""
import os
import stat
from dataclasses import dataclass
from contextlib import contextmanager

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


@contextmanager
def _pin(path, *, authorized, cancelled, allow_empty):
    """Open only metadata, never the file contents or a special device."""
    _check(authorized, cancelled)
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise Hold("csv_invalid_selection")
    parts = path.split("/")[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise Hold("csv_invalid_selection")
    if not all(hasattr(os, flag) for flag in
               ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC", "O_PATH")):
        raise Hold("csv_secure_reader_unavailable")
    if os.open not in os.supports_dir_fd:
        raise Hold("csv_secure_reader_unavailable")
    directory = None
    pinned = None
    try:
        base_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open("/", base_flags | os.O_DIRECTORY)
        for part in parts[:-1]:
            _check(authorized, cancelled)
            child = os.open(part, base_flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        _check(authorized, cancelled)
        # O_PATH pins the inode without opening a device or blocking on a FIFO.
        pinned = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=directory)
        before = os.fstat(pinned)
        if not stat.S_ISREG(before.st_mode):
            raise Hold("csv_not_regular_file")
        if (before.st_size == 0 and not allow_empty) or before.st_size > MAX_BYTES:
            raise Hold("csv_invalid_size")
        _check(authorized, cancelled)
        yield pinned, before
    except OSError:
        raise Hold("csv_selection_unreadable") from None
    finally:
        if pinned is not None:
            os.close(pinned)
        if directory is not None:
            os.close(directory)


def selection_identity(path, *, authorized, cancelled=lambda: False, allow_empty=False):
    """Capture regular-file identity at trusted picker acceptance, without data I/O.

    GTK returns a pathname, not a descriptor. This records identity at the
    acceptance callback, not at an earlier highlight in a native dialog.
    """
    with _pin(path, authorized=authorized, cancelled=cancelled,
              allow_empty=allow_empty) as (_, before):
        _check(authorized, cancelled)
        return _identity(before)


def read_selected_bytes(path, *, authorized, cancelled=lambda: False,
                        allow_empty=False, expected_identity=None):
    """Reopen a selected inode safely; refuse replacement before reading data.

    The trusted picker supplies expected_identity. Standalone synchronous
    callers may omit it when the actual open is their selection boundary.
    This is a metadata identity contract, not mount or same-UID isolation.
    """
    fd = None
    try:
        with _pin(path, authorized=authorized, cancelled=cancelled,
                  allow_empty=allow_empty) as (pinned, before):
            if expected_identity is not None and _identity(before) != expected_identity:
                raise Hold("csv_selection_changed")
            _check(authorized, cancelled)
            # Reopen the pinned inode, not the selected pathname.
            fd = os.open("/proc/self/fd/%d" % pinned,
                         os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            if _identity(os.fstat(fd)) != _identity(before):
                raise Hold("csv_changed_during_read")
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
        return bytes(data)
    except OSError:
        # Do not propagate private paths or raw system diagnostics.
        raise Hold("csv_selection_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)


def read_selected_csv(path, *, authorized, cancelled=lambda: False, expected_identity=None):
    """Preserve strict nonempty CSV contract and optional picker identity."""
    return SelectedCSV(read_selected_bytes(path, authorized=authorized,
                                          cancelled=cancelled, expected_identity=expected_identity))
