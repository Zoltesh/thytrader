"""HTTP helpers for agent desks (ADR 0147)."""

from __future__ import annotations

from urllib.parse import quote

from thytrader.agent_http import request_json, request_mutation_json

DESKS_API_PREFIX = "/api/v1/desks"


def create_desk(base_url: str, payload: dict[str, object]) -> object:
    """Create a desk (paper, or live with the acknowledgement)."""
    return request_mutation_json(
        method="POST", url=f"{base_url}{DESKS_API_PREFIX}", payload=payload
    )


def list_desks(base_url: str) -> object:
    """Every desk, newest first."""
    return request_json(method="GET", url=f"{base_url}{DESKS_API_PREFIX}")


def show_desk(base_url: str, desk_id: str) -> object:
    """One desk with its books."""
    return request_json(method="GET", url=f"{base_url}{_desk(desk_id)}")


def desk_briefing(base_url: str, desk_id: str) -> object:
    """Settle finished books and return the briefing with its harness prompt."""
    return request_json(method="GET", url=f"{base_url}{_desk(desk_id)}/briefing", timeout=120.0)


def record_wake(base_url: str, desk_id: str, payload: dict[str, object]) -> object:
    """Hand ThyTrader one model answer (or a harness failure) to apply and journal."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_desk(desk_id)}/wakes",
        payload=payload,
        timeout=120.0,
    )


def skip_wake(base_url: str, desk_id: str, payload: dict[str, object]) -> object:
    """Journal a skipped wake if nothing changed since the newest one, else say why not."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_desk(desk_id)}/wakes/skip",
        payload=payload,
        timeout=120.0,
    )


def update_desk(base_url: str, desk_id: str, payload: dict[str, object]) -> object:
    """Change a desk's name, harness, model, cadence, mandate or caps."""
    return request_mutation_json(method="PATCH", url=f"{base_url}{_desk(desk_id)}", payload=payload)


def list_wakes(base_url: str, desk_id: str, *, limit: int) -> object:
    """The newest wakes, newest first."""
    return request_json(method="GET", url=f"{base_url}{_desk(desk_id)}/wakes?limit={limit}")


def desk_score(base_url: str, desk_id: str) -> object:
    """The desk's return beside its buy-and-hold benchmarks."""
    return request_json(method="GET", url=f"{base_url}{_desk(desk_id)}/score", timeout=120.0)


def set_desk_status(
    base_url: str, desk_id: str, status: str, *, i_understand_live: bool = False
) -> object:
    """Pause, resume or retire a desk; resuming a live desk needs ``i_understand_live``."""
    payload: dict[str, object] = {"status": status}
    if i_understand_live:
        payload["i_understand_live"] = True
    return request_mutation_json(
        method="POST", url=f"{base_url}{_desk(desk_id)}/status", payload=payload
    )


def _desk(desk_id: str) -> str:
    """The path of one desk, with its id escaped."""
    return f"{DESKS_API_PREFIX}/{quote(desk_id, safe='')}"
