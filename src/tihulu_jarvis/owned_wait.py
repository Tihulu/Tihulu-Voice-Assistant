# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded authorization checks for owned asynchronous I/O, not a grant broker."""
import asyncio


async def await_authorized(factory, timeout, checkpoint, *, interval=.25):
    """Await owned work with bounded grant checks and drain on refusal.

    checkpoint is a trusted synchronous callback that raises Hold on denial.
    The factory owns its transport or child cleanup; denial drains its task.
    """
    checkpoint()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async def start():
        checkpoint()
        return await factory()
    task = asyncio.create_task(start())
    try:
        while True:
            checkpoint()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            done, _ = await asyncio.wait((task,), timeout=min(interval, remaining))
            checkpoint()
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        # Retrieve exceptions even when authorization failed simultaneously.
        await asyncio.gather(task, return_exceptions=True)


