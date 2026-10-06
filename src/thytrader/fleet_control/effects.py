"""Operator-facing wording for fleet actions.

These sentences are the contract the CLI, HTTP preview, and UI must show.
Disarm never describes cancellation or flattening.
"""

from __future__ import annotations

from thytrader.fleet_control.models import FleetAction, FleetModeScope


def effect_text(action: FleetAction) -> str:
    """Return the one-sentence effect of an action, independent of mode."""
    if action is FleetAction.DISARM:
        return (
            "Inhibits new starts and entries for this mode until an explicit rearm. "
            "Does not pause books, cancel orders, flatten positions, or change residual inventory."
        )
    if action is FleetAction.MANAGED_STOP:
        return (
            "Records managed shutdown on each confirmed book. The worker cancels "
            "risk-increasing entry orders and keeps protective orders. Residual positions "
            "stay in account risk. This is not a flatten and not a pause. "
            "Venue work is asynchronous."
        )
    if action is FleetAction.FLATTEN:
        return (
            "Records an explicit flatten on each confirmed book. The worker marketably exits "
            "residual inventory, then cancels remainders. This is not disarm and not a pause. "
            "Exit price and fees are not guaranteed. Venue work is asynchronous."
        )
    return (
        "Clears entry inhibition so later starts and entries may be admitted. "
        "Does not resume, start, or re-arm individual books. A live scope requires "
        "i_understand_live. Paused and stopped books stay as they are."
    )


def cancels_entries(action: FleetAction) -> bool:
    """True only when the action records a command that cancels entry orders."""
    return action in {FleetAction.MANAGED_STOP, FleetAction.FLATTEN}


def flattens(action: FleetAction) -> bool:
    """True only for the explicit flatten action."""
    return action is FleetAction.FLATTEN


def pauses(action: FleetAction) -> bool:
    """Fleet actions do not pause. Pause remains a per-book command."""
    del action
    return False


def requires_live_acknowledgement(action: FleetAction, mode: FleetModeScope) -> bool:
    """Live rearm and any flatten that can include live books need the live ack."""
    includes_live = mode in {FleetModeScope.LIVE, FleetModeScope.ALL}
    if action is FleetAction.REARM:
        return includes_live
    if action is FleetAction.FLATTEN:
        return includes_live
    return False
