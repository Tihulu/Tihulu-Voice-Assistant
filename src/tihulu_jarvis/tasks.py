# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""A local board for the work this client actually runs.

A task states its kind, its label, its state and when it started and ended —
never a guessed percentage. Progress is a number only when the thing being
counted is real (a download's bytes); for everything else the state is the
truth: queued, running, done, cancelled or failed. Each task also names the
resource leases it holds, so a reader can see why a device is busy.
"""
import time
from dataclasses import dataclass, replace

from .voice.m1 import Hold

MAX_TASKS = 64
STATES = ("queued", "running", "done", "cancelled", "failed")
RUNNING = ("queued", "running")
KINDS = ("turn", "download", "transcription", "catalog_fetch", "update", "file_research")


@dataclass(frozen=True)
class Task:
    id: int
    kind: str
    label: str
    state: str = "queued"
    started_at: float = 0.0
    ended_at: float = 0.0
    reason: str = ""
    locks: tuple = ()
    counted: int = 0        # bytes or nothing at all; never a percentage
    counted_total: int = 0

    def checked(self):
        if self.kind not in KINDS:
            raise Hold("unknown_task_kind")
        if self.state not in STATES:
            raise Hold("invalid_task_state")
        if not isinstance(self.label, str) or len(self.label) > 120:
            raise Hold("invalid_task_label")
        if (type(self.counted) is not int or type(self.counted_total) is not int
                or self.counted < 0 or self.counted_total < 0):
            raise Hold("invalid_task_count")
        return self

    @property
    def busy(self):
        return self.state in RUNNING

    def elapsed(self, now=0.0):
        if not self.started_at:
            return 0.0
        end = self.ended_at if not self.busy and self.ended_at else (now or time.time())
        return max(0.0, end - self.started_at)


class Board:
    """A bounded, append-mostly board. Nothing is ever guessed."""

    def __init__(self):
        self.rows = ()
        self._next = 1

    def start(self, kind, label, locks=(), now=0.0):
        label = label if isinstance(label, str) else ""
        if not label.strip() or len(label) > 120:
            raise Hold("invalid_task_label")
        task = Task(self._next, kind, label.strip()[:120], "running",
                    now or time.time(), 0.0, "", tuple(locks)).checked()
        self.rows = tuple(self.rows)[-(MAX_TASKS - 1):] + (task,)
        self._next += 1
        return task.id

    def update(self, task_id, **changes):
        rows = []
        for row in self.rows:
            if row.id == task_id:
                row = replace(row, **changes).checked()
            rows.append(row)
        self.rows = tuple(rows)
        return self.get(task_id)

    def finish(self, task_id, state="done", reason="", now=0.0):
        return self.update(task_id, state=state, reason=str(reason)[:80],
                           ended_at=now or time.time())

    def cancel(self, task_id, reason="cancelled", now=0.0):
        return self.finish(task_id, "cancelled", reason, now)

    def get(self, task_id):
        for row in self.rows:
            if row.id == task_id:
                return row
        raise Hold("task_not_found")

    def busy(self):
        return tuple(row for row in self.rows if row.busy)

    def locks(self):
        """Every lease currently held, so a busy device is explained."""
        out = []
        for row in self.busy():
            for resource in row.locks:
                out.append((row.id, resource))
        return tuple(out)
