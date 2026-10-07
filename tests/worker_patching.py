"""Patch a global of the execution-worker cycle in every module that looks it up.

The worker cycle once lived in ``thytrader.execution_worker.service`` alone, so one
``monkeypatch.setattr(service, name, value)`` reached every lookup of ``name``. It is now
split across sibling modules, and a monkeypatch only takes effect in the module whose
globals the calling function reads. ``patch_worker_global`` restores the old reach: it
rebinds ``name`` in every worker-cycle module that binds it at runtime, and nowhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution_worker import (
    discretionary_step,
    live_sizing,
    ports,
    service,
    strategy_step,
    supervision,
    windows,
)

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

WORKER_CYCLE_MODULES: tuple[ModuleType, ...] = (
    service,
    ports,
    windows,
    supervision,
    live_sizing,
    strategy_step,
    discretionary_step,
)


def patch_worker_global(
    monkeypatch: pytest.MonkeyPatch, name: str, value: object
) -> tuple[str, ...]:
    """Bind ``value`` to ``name`` in every worker-cycle module that binds it.

    Raises when no module binds ``name`` (a stale patch target) or when the modules bind
    different objects (the patch would no longer replace one shared global). Returns the
    patched module names.
    """
    bound = [module for module in WORKER_CYCLE_MODULES if name in vars(module)]
    if not bound:
        msg = f"no execution-worker module binds {name!r}"
        raise AttributeError(msg)
    if len({id(vars(module)[name]) for module in bound}) != 1:
        msg = f"execution-worker modules bind different objects to {name!r}"
        raise AssertionError(msg)
    for module in bound:
        monkeypatch.setattr(module, name, value)
    return tuple(module.__name__ for module in bound)
