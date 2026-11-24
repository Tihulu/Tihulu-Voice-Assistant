# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded authorization checks for owned asynchronous I/O, not a grant broker."""
import asyncio


class OperationDeadline:
    """One operation-local monotonic budget shared by waits and effect checks."""

    def __init__(self, timeout, checkpoint):
        checkpoint()
        self._checkpoint = checkpoint
        self._loop = asyncio.get_running_loop()
        self._expires = self._loop.time() + timeout

    def remaining(self):
        return self._expires - self._loop.time()

    def check(self):
        # Preserve authorization failure priority, including if both expire.
        self._checkpoint()
        if self.remaining() <= 0:
            raise asyncio.TimeoutError


async def finish_owned(awaitable):
    """Drain an owned cleanup despite further cancellation of its caller.

    The cleanup future is not independently cancellable by its caller.
    Propagate cancellation only after it finishes; propagate cleanup errors.
    """
    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result


async def await_with_deadline(factory, deadline, *, interval=.25):
    """Await and drain owned work; effect sites must also call deadline.check."""
    async def start():
        deadline.check()
        return await factory()
    task = asyncio.create_task(start())
    try:
        while True:
            deadline.check()
            remaining = deadline.remaining()
            if remaining <= 0:
                raise asyncio.TimeoutError
            done, _ = await asyncio.wait((task,), timeout=min(interval, remaining))
            deadline.check()
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        # Retrieve exceptions even when authorization failed simultaneously.
        await finish_owned(asyncio.gather(task, return_exceptions=True))


async def await_authorized(factory, timeout, checkpoint, *, interval=.25):
    """Await owned work with bounded grant checks and drain on refusal.

    For work with internal effect boundaries, share an OperationDeadline
    explicitly with the worker rather than relying on the outer wait alone.
    """
    return await await_with_deadline(factory, OperationDeadline(timeout, checkpoint),
                                     interval=interval)
