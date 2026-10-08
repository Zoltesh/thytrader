"""Durable entry-inhibition checks shared by starts and entry intents.

Exits, protection, and cancellations do not call these helpers. Missing tables,
rows, or unreadable state fail closed for risk-increasing actions only.
"""

from __future__ import annotations

from thytrader.fleet_control import ENTRY_INHIBITED_PREFIX
from thytrader.trading.entry_latch import remember_entry_inhibition
from thytrader.trading.models import ExecutionStoreError


def entry_inhibited_detail(mode: str, *, action: str) -> str:
    """Stable conflict text for a refused start or entry."""
    noun = "starts" if action == "start" else "entries"
    return (
        f"{ENTRY_INHIBITED_PREFIX}: New {mode} {noun} are refused until an explicit "
        "fleet rearm. Disarm does not flatten, cancel, or pause residual positions."
    )


def remember_snapshot(*, paper: bool, live: bool) -> None:
    """Publish a latch snapshot for the synchronous entry gate in this process."""
    remember_entry_inhibition({"paper": paper, "live": live})


async def refresh_process_entry_inhibition(store: object) -> None:
    """Load the latch into this process before a worker cycle admits entries.

    An absent reader or failed read inhibits entries only; the caller still
    runs exits, protection, and reconciliation.
    """
    reader = getattr(store, "read_entry_inhibition", None)
    if not callable(reader):
        remember_entry_inhibition({"paper": True, "live": True})
        return
    try:
        snapshot = await reader()
    except ExecutionStoreError:
        remember_entry_inhibition({"paper": True, "live": True})
        return
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("paper"), bool)
        or not isinstance(snapshot.get("live"), bool)
    ):
        remember_entry_inhibition({"paper": True, "live": True})
        return
    remember_entry_inhibition({"paper": snapshot["paper"], "live": snapshot["live"]})
