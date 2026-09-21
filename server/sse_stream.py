"""Production-grade SSE Keepalive & Write-Timeout Stream Guard.

Protects FastAPI StreamingResponse endpoints by:
1. Emitting periodic keepalive comments (: keepalive\\n\\n) or Anthropic pings when upstream is waiting.
2. Enforcing a maximum write timeout (default 60s) for stuck/unresponsive upstreams or frozen clients.
3. Ensuring proper cancellation and resource cleanup when clients disconnect.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, AsyncIterator, Callable, Iterator, Union

_log = logging.getLogger(__name__)

DEFAULT_KEEPALIVE_SECONDS = 15.0
DEFAULT_WRITE_TIMEOUT_SECONDS = 60.0
SSE_KEEPALIVE_COMMENT = ": keepalive\n\n"
ANTHROPIC_PING = f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def async_sse_guard(
    generator: Union[Iterator[str], AsyncIterator[str]],
    *,
    keepalive_interval: float = DEFAULT_KEEPALIVE_SECONDS,
    write_timeout: float = DEFAULT_WRITE_TIMEOUT_SECONDS,
    heartbeat: str | Callable[[], str] = SSE_KEEPALIVE_COMMENT,
) -> AsyncIterator[str]:
    """Wraps either a sync generator or an async generator with SSE keepalive and 60s timeout protection."""
    is_async = hasattr(generator, "__anext__")
    
    # If it's a sync generator, pull items in a thread pool to avoid blocking the asyncio event loop
    loop = asyncio.get_running_loop()
    
    def _next_sync(it):
        try:
            return False, next(it)
        except StopIteration:
            return True, None

    keepalive_step = max(float(keepalive_interval), 0.1)
    timeout_limit = max(float(write_timeout), 5.0)

    try:
        while True:
            if is_async:
                fetch_coro = generator.__anext__()
                pending = asyncio.ensure_future(fetch_coro)
            else:
                pending = asyncio.ensure_future(loop.run_in_executor(None, _next_sync, generator))

            time_waited = 0.0
            done = False

            while not done:
                done_set, _ = await asyncio.wait({pending}, timeout=keepalive_step)
                if done_set:
                    done = True
                    break

                time_waited += keepalive_step
                if time_waited >= timeout_limit:
                    _log.warning(
                        "[SSEGuard] Stream idle/hung for %.1fs exceeding limit (%.1fs). Aborting stream.",
                        time_waited,
                        timeout_limit,
                    )
                    pending.cancel()
                    return

                # Send keepalive heartbeat while waiting
                yield heartbeat() if callable(heartbeat) else heartbeat

            try:
                res = pending.result()
                if is_async:
                    item = res
                else:
                    is_stop, item = res
                    if is_stop:
                        break
            except (StopAsyncIteration, StopIteration):
                break

            yield item

    finally:
        close_fn = getattr(generator, "close", getattr(generator, "aclose", None))
        if close_fn is not None:
            try:
                if asyncio.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
            except Exception:
                pass
