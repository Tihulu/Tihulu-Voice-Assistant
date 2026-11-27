# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only observation of a disposable PID namespace, not a process broker.

Compare exact PID/start-time identities before and after an owned wrapper.
This is evidence at two instants, not proof of host process ownership or of
everything that happened between observations. No signaling or termination.
"""
from pathlib import Path
import os
import re

from gateway import Refused


_PID_NS = re.compile(r"pid:\[[0-9]+\]\Z")


def process_identity(pid, raw):
    """Parse Linux proc stat field 22 without splitting the comm field."""
    if type(pid) is not int or pid <= 0 or type(raw) is not bytes or not 1 <= len(raw) <= 8192:
        raise Refused("fixture_proc_stat")
    prefix = str(pid).encode("ascii") + b" ("
    if not raw.startswith(prefix):
        raise Refused("fixture_proc_pid")
    _, sep, suffix = raw.rpartition(b")")
    if not sep or not suffix.startswith(b" "):
        raise Refused("fixture_proc_stat")
    fields = suffix.split()
    if len(fields) < 20 or len(fields[0]) != 1 or not fields[19].isdigit():
        raise Refused("fixture_proc_stat")
    if len(fields[19]) > 20:
        raise Refused("fixture_proc_start")
    started = int(fields[19])
    if started < 0 or started >= 2**64:
        raise Refused("fixture_proc_start")
    return pid, started


def private_process_snapshot(host_pid_namespace):
    """Only inspect proc after verifying the current namespace is different."""
    if type(host_pid_namespace) is not str or not _PID_NS.fullmatch(host_pid_namespace):
        raise Refused("fixture_host_pid_namespace")
    current = os.readlink("/proc/self/ns/pid")
    if not _PID_NS.fullmatch(current) or current == host_pid_namespace:
        raise Refused("fixture_not_private_pid_namespace")
    identities = []
    for entry in Path("/proc").iterdir():
        if entry.name.isascii() and entry.name.isdecimal():
            try:
                with (entry / "stat").open("rb") as stream:
                    raw = stream.read(8193)
            except OSError as exc:
                # A disappearing process cannot be silently ignored.
                raise Refused("fixture_proc_unstable") from exc
            identities.append(process_identity(int(entry.name), raw))
    if not identities or not any(pid == os.getpid() for pid, _ in identities):
        raise Refused("fixture_proc_missing_self")
    return tuple(sorted(identities))


def namespace_drained(before, after):
    """Require identical baseline identities, including PID reuse detection."""
    if type(before) is not tuple or type(after) is not tuple or not before or not after:
        return False
    for snapshot in (before, after):
        if any(type(row) is not tuple or len(row) != 2 or
               type(row[0]) is not int or row[0] <= 0 or
               type(row[1]) is not int or not 0 <= row[1] < 2**64
               for row in snapshot):
            return False
        if len({row[0] for row in snapshot}) != len(snapshot):
            return False
    return tuple(sorted(before)) == tuple(sorted(after))
