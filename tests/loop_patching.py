"""Patch a global of the closed-bar execution loop in every module that looks it up.

The closed-bar loop once lived in ``thytrader.execution.loop`` alone, so one
``monkeypatch.setattr("thytrader.execution.loop.<name>", value)`` reached every lookup of
``name``. It is now split across sibling modules, and a monkeypatch only takes effect in
the module whose globals the calling function reads. ``patch_loop_global`` restores the
old reach: it rebinds ``name`` in every closed-bar loop module that binds it at runtime,
and nowhere else (discretionary and stopped books keep their own bindings, as before).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution import (
    breaker_pause,
    entry,
    entry_admission,
    entry_reprice,
    entry_sizing,
    exits,
    live_protection,
    loop,
    residual,
    runtime_ops,
)

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

LOOP_MODULES: tuple[ModuleType, ...] = (
    loop,
    runtime_ops,
    breaker_pause,
    exits,
    live_protection,
    residual,
    entry,
    entry_sizing,
    entry_admission,
    entry_reprice,
)


def patch_loop_global(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> tuple[str, ...]:
    """Bind ``value`` to ``name`` in every closed-bar loop module that binds it.

    Raises when no module binds ``name`` (a stale patch target) or when the modules bind
    different objects (the patch would no longer replace one shared global). Returns the
    patched module names.
    """
    bound = [module for module in LOOP_MODULES if name in vars(module)]
    if not bound:
        msg = f"no closed-bar loop module binds {name!r}"
        raise AttributeError(msg)
    if len({id(vars(module)[name]) for module in bound}) != 1:
        msg = f"closed-bar loop modules bind different objects to {name!r}"
        raise AssertionError(msg)
    for module in bound:
        monkeypatch.setattr(module, name, value)
    return tuple(module.__name__ for module in bound)
