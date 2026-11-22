# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""What this process uses, measured rather than claimed.

The claim "an idle window costs little" is only worth making if the number can
be read. This reads the process's own resident memory and thread count from
`/proc/self/status` on Linux, and says honestly that it cannot measure elsewhere
instead of producing a number it cannot stand behind.
"""
import os

RESIDENT = "VmRSS"
THREADS = "Threads"


def _field(name):
    """One integer field of `/proc/self/status`, or None when unavailable."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(name + ":"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def resident_kib():
    """This process's resident memory in KiB, or None where it cannot be read."""
    return _field(RESIDENT)


def threads():
    """This process's thread count, or None where it cannot be read."""
    return _field(THREADS)


def resident_mib():
    """This process's resident memory in MiB, or None where it cannot be read."""
    kib = resident_kib()
    return None if kib is None else max(1, round(kib / 1024))


def summary():
    """One short English line: what this process uses right now, or that it
    cannot be measured on this platform."""
    mib = resident_mib()
    count = threads()
    if mib is None:
        return "this process: resident memory is not readable on this platform"
    line = "this process: %d MiB resident" % mib
    if count is not None:
        line += ", %d threads" % count
    return line
