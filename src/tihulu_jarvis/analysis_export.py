# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic, local-only exports from trusted analysis artifacts."""
import csv
import hashlib
import io
import os
from uuid import uuid4

from .analysis_controller import Artifact
from .voice.m1 import Hold

MAX_EXPORT = 4 * 1024 * 1024
FORMATS = {
    "svg": ".svg", "png": ".png",
    "data": ".csv", "summary": ".csv",
}


def summary_rows(series):
    return (
        ("count", series.count, "samples"),
        ("mean", series.mean_c, "degC"),
        ("minimum", series.minimum_c, "degC"),
        ("maximum", series.maximum_c, "degC"),
        ("change", series.delta_c, "degC"),
        ("duration", series.duration_s, "s"),
    )


def _csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def encode(artifact, kind, *, png=None):
    """Only internal artifacts and, for PNG, a trusted native encoder."""
    if not isinstance(artifact, Artifact):
        raise Hold("export_invalid_artifact")
    if hashlib.sha256(artifact.svg).hexdigest() != artifact.svg_sha256:
        raise Hold("export_digest_mismatch")
    if kind == "svg":
        data = artifact.svg
    elif kind == "data":
        data = _csv((("time_s", "temperature_c"), *artifact.series.points))
    elif kind == "summary":
        data = _csv((("metric", "value", "unit"), *summary_rows(artifact.series)))
    elif kind == "png":
        if not callable(png):
            raise Hold("export_png_unavailable")
        data = png(artifact.svg)
        if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise Hold("export_png_invalid")
    else:
        raise Hold("export_invalid_format")
    if not data or len(data) > MAX_EXPORT:
        raise Hold("export_invalid_size")
    return data


def write_new(path, kind, data, *, authorized):
    """Publish a new private file; never replace an existing entry.

    Linux directory-relative traversal rejects symlink components. A temporary
    file is written within the pinned destination directory and linked to the
    final name atomically, without replacement. Caller owns the authorization
    and must bind it to the exact destination and artifact. No directories are
    created. No claim of mount/namespace isolation or crash durability.
    """
    if authorized() is not True:
        raise Hold("export_not_authorized")
    if not isinstance(kind, str) or kind not in FORMATS:
        raise Hold("export_invalid_format")
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise Hold("export_invalid_destination")
    parts = path.split("/")[1:]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise Hold("export_invalid_destination")
    if not parts[-1].lower().endswith(FORMATS[kind]):
        raise Hold("export_wrong_extension")
    if not isinstance(data, bytes) or not data or len(data) > MAX_EXPORT:
        raise Hold("export_invalid_size")
    if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")):
        raise Hold("export_secure_writer_unavailable")
    if not {os.open, os.link, os.unlink}.issubset(os.supports_dir_fd):
        raise Hold("export_secure_writer_unavailable")
    directory = None
    fd = None
    temp = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open("/", flags)
        for part in parts[:-1]:
            if authorized() is not True:
                raise Hold("export_not_authorized")
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        if authorized() is not True:
            raise Hold("export_not_authorized")
        name = ".jarvis-" + uuid4().hex + ".tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                     os.O_CLOEXEC, 0o600, dir_fd=directory)
        temp = name
        offset = 0
        while offset < len(data):
            if authorized() is not True:
                raise Hold("export_not_authorized")
            size = os.write(fd, data[offset:offset + 65536])
            if size <= 0:
                raise Hold("export_write_failed")
            offset += size
        os.fsync(fd)
        if authorized() is not True:
            raise Hold("export_not_authorized")
        # No replace: even a symlink or directory at the target is refused.
        os.link(temp, parts[-1], src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
    except FileExistsError:
        raise Hold("export_destination_exists") from None
    except OSError:
        raise Hold("export_write_failed") from None
    finally:
        if fd is not None:
            os.close(fd)
        if temp is not None:
            os.unlink(temp, dir_fd=directory)
        if directory is not None:
            os.close(directory)
