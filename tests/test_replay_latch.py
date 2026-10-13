"""Task-local captured latch inputs never replace the production process-wide cache."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from thytrader.trading.entry_latch import (
    clear_entry_inhibition_cache,
    process_entry_inhibited,
    remember_entry_inhibition,
)
from thytrader.trading.models import DeploymentMode


@pytest.mark.anyio
async def test_replay_entry_latch_is_task_local_and_restores_fail_closed_default() -> None:
    """Offline recorded entry authority must not alter concurrent production/default behavior."""
    module = importlib.import_module("thytrader.trading.entry_latch")
    assert hasattr(module, "entry_inhibition_scope"), "recorded fleet latch seam missing"
    scope = module.entry_inhibition_scope
    clear_entry_inhibition_cache()
    assert process_entry_inhibited(DeploymentMode.LIVE)
    ready, done = asyncio.Event(), asyncio.Event()

    async def replay() -> None:
        """Use the recorded permit in only this coroutine, including failure restoration."""
        with scope(DeploymentMode.LIVE, False):
            assert not process_entry_inhibited(DeploymentMode.LIVE)
            assert process_entry_inhibited(DeploymentMode.PAPER)
            ready.set()
            await done.wait()
        assert process_entry_inhibited(DeploymentMode.LIVE)

    task = asyncio.create_task(replay())
    await ready.wait()
    assert process_entry_inhibited(DeploymentMode.LIVE)
    done.set()
    await task
    remember_entry_inhibition({"live": False, "paper": False})
    try:
        with scope(DeploymentMode.LIVE, True), pytest.raises(RuntimeError):
            assert process_entry_inhibited(DeploymentMode.LIVE)
            raise RuntimeError("synthetic failure")
        assert not process_entry_inhibited(DeploymentMode.LIVE)
    finally:
        clear_entry_inhibition_cache()
