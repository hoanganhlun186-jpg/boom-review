"""Run independent async work from synchronous pipeline code."""

import asyncio
from concurrent.futures import ThreadPoolExecutor


def run_async_task(async_function, *args, **kwargs):
    """Create and await the coroutine on a loop owned by this call.

    A caller such as Playwright may already own the current thread's loop.
    In that case use a worker, propagating its result or original exception.
    Only use for independent tasks, not objects bound to the caller's loop.
    """
    def run():
        return asyncio.run(async_function(*args, **kwargs))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="recap-async") as pool:
        return pool.submit(run).result()
