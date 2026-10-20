"""HTTP helpers for agent desks (ADR 0147)."""

from __future__ import annotations

from urllib.parse import quote

from thytrader.agent_http import request_json, request_mutation_json

DESKS_API_PREFIX = "/api/v1/desks"


def create_desk(base_url: str, payload: dict[str, object]) -> object:
    """Create a paper desk."""
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


def list_wakes(base_url: str, desk_id: str, *, limit: int) -> object:
    """The newest wakes, newest first."""
    return request_json(method="GET", url=f"{base_url}{_desk(desk_id)}/wakes?limit={limit}")


def desk_score(base_url: str, desk_id: str) -> object:
    """The desk's return beside its buy-and-hold benchmarks."""
    return request_json(method="GET", url=f"{base_url}{_desk(desk_id)}/score", timeout=120.0)


def set_desk_status(base_url: str, desk_id: str, status: str) -> object:
    """Pause, resume or retire a desk."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_desk(desk_id)}/status",
        payload={"status": status},
    )


def _desk(desk_id: str) -> str:
    """The path of one desk, with its id escaped."""
    return f"{DESKS_API_PREFIX}/{quote(desk_id, safe='')}"
