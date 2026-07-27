"""Core lifecycle for the continuously running ThyTrader worker."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from thytrader.runtime import RuntimeState


async def run_worker(runtime: RuntimeState, stop_requested: asyncio.Event) -> None:
    """Run until graceful shutdown is requested while publishing readiness."""
    runtime.ready = True
    try:
        await stop_requested.wait()
    finally:
        runtime.ready = False
