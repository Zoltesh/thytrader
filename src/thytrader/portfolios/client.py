"""Loopback HTTP client for the ``thytrader-portfolio`` CLI (ADR 0088, ADR 0091).

Reads use plain JSON GETs; mutations go through ``request_mutation_json`` so the
installation credential is attached when the trust boundary is enabled. This lane reads
the deployment and the manager briefing and submits or decides proposals; it never
starts, stops, or places orders (portfolio start/pause/resume/stop are
``thytrader-runtime`` commands, and no route places an order).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from thytrader.agent_http import AgentHttpError, request_json, request_mutation_json

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.portfolios.backtest import PortfolioBacktestRequest
    from thytrader.portfolios.models import (
        PortfolioCreateRequest,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        SleeveAddRequest,
        SleevesAddRequest,
    )
    from thytrader.portfolios.proposals import ProposalDecisionRequest, ProposalSubmitRequest

JsonObject = dict[str, object]
"""One decoded JSON object from the API (validated by the API's response models)."""

_PREFIX = "/api/v1/portfolios"


def list_portfolios(base_url: str, *, limit: int, cursor: str | None) -> JsonObject:
    """GET one page of portfolios (oldest first)."""
    query = {"limit": str(limit)}
    if cursor is not None:
        query["cursor"] = cursor
    return _get(f"{base_url}{_PREFIX}?{urlencode(query)}", "portfolio list")


def show_portfolio(base_url: str, portfolio_id: UUID) -> JsonObject:
    """GET one portfolio with sleeves, allocation, limits, and manager settings."""
    return _get(f"{base_url}{_PREFIX}/{portfolio_id}", "portfolio")


def create_portfolio(base_url: str, request: PortfolioCreateRequest) -> JsonObject:
    """POST one new portfolio."""
    return _mutate("POST", f"{base_url}{_PREFIX}", request.model_dump(mode="json"), "portfolio")


def update_portfolio(
    base_url: str, portfolio_id: UUID, request: PortfolioUpdateRequest
) -> JsonObject:
    """PATCH settings, limits, or manager settings (revision-guarded)."""
    return _mutate(
        "PATCH",
        f"{base_url}{_PREFIX}/{portfolio_id}",
        request.model_dump(mode="json", exclude_none=True),
        "portfolio",
    )


def add_sleeve(base_url: str, portfolio_id: UUID, request: SleeveAddRequest) -> JsonObject:
    """POST one sleeve (revision-guarded)."""
    return _mutate(
        "POST",
        f"{base_url}{_PREFIX}/{portfolio_id}/sleeves",
        request.model_dump(mode="json", exclude_none=True),
        "portfolio",
    )


def add_sleeves(base_url: str, portfolio_id: UUID, request: SleevesAddRequest) -> JsonObject:
    """POST several sleeves at once (one revision, all or none)."""
    return _mutate(
        "POST",
        f"{base_url}{_PREFIX}/{portfolio_id}/sleeves/batch",
        request.model_dump(mode="json", exclude_none=True),
        "portfolio",
    )


def delete_portfolio(base_url: str, portfolio_id: UUID, *, revision: int) -> JsonObject:
    """DELETE one portfolio (revision-guarded; refused while any sleeve is deployed)."""
    query = urlencode({"revision": str(revision)})
    return _mutate("DELETE", f"{base_url}{_PREFIX}/{portfolio_id}?{query}", None, "deletion")


def remove_sleeve(
    base_url: str, portfolio_id: UUID, sleeve_id: UUID, *, revision: int
) -> JsonObject:
    """DELETE one sleeve (revision-guarded)."""
    query = urlencode({"revision": str(revision)})
    return _mutate(
        "DELETE",
        f"{base_url}{_PREFIX}/{portfolio_id}/sleeves/{sleeve_id}?{query}",
        None,
        "portfolio",
    )


def set_weights(base_url: str, portfolio_id: UUID, request: SetWeightsRequest) -> JsonObject:
    """PUT every sleeve weight (and optionally the cash reserve)."""
    return _mutate(
        "PUT",
        f"{base_url}{_PREFIX}/{portfolio_id}/weights",
        request.model_dump(mode="json", exclude_none=True),
        "portfolio",
    )


def submit_backtest(
    base_url: str, portfolio_id: UUID, request: PortfolioBacktestRequest
) -> JsonObject:
    """POST one async portfolio backtest (HTTP 202 with the queued job)."""
    return _mutate(
        "POST",
        f"{base_url}{_PREFIX}/{portfolio_id}/backtests",
        request.model_dump(mode="json", exclude_none=True),
        "portfolio backtest",
        timeout=120.0,
    )


def show_backtest_job(base_url: str, portfolio_id: UUID, job_id: UUID) -> JsonObject:
    """GET one portfolio backtest job."""
    return _get(
        f"{base_url}{_PREFIX}/{portfolio_id}/backtests/jobs/{job_id}", "portfolio backtest job"
    )


def show_backtest_result(
    base_url: str, portfolio_id: UUID, result_fingerprint: str, *, max_points: int | None
) -> JsonObject:
    """GET one stored portfolio backtest (curve thinned to ``max_points`` when given)."""
    suffix = "" if max_points is None else f"?{urlencode({'max_points': str(max_points)})}"
    return _get(
        f"{base_url}{_PREFIX}/{portfolio_id}/backtests/{result_fingerprint}{suffix}",
        "portfolio backtest",
    )


def list_backtests(base_url: str, portfolio_id: UUID, *, limit: int) -> JsonObject:
    """GET stored portfolio backtests (newest first) and recent jobs."""
    query = urlencode({"limit": str(limit)})
    results = _get(f"{base_url}{_PREFIX}/{portfolio_id}/backtests?{query}", "portfolio backtests")
    jobs = _get(f"{base_url}{_PREFIX}/{portfolio_id}/backtests/jobs?{query}", "backtest jobs")
    return {"results": results, "jobs": jobs}


def list_journal(
    base_url: str, portfolio_id: UUID, *, limit: int, cursor: str | None
) -> JsonObject:
    """GET the portfolio journal newest first."""
    query = {"limit": str(limit)}
    if cursor is not None:
        query["cursor"] = cursor
    return _get(f"{base_url}{_PREFIX}/{portfolio_id}/journal?{urlencode(query)}", "journal")


def show_deployment(base_url: str, portfolio_id: UUID) -> JsonObject:
    """GET the portfolio's deployment: state, sleeve bots, breakers, exposure."""
    return _get(f"{base_url}{_PREFIX}/{portfolio_id}/deployment", "portfolio deployment")


def show_briefing(
    base_url: str, portfolio_id: UUID, *, decisions_per_sleeve: int, journal_limit: int
) -> JsonObject:
    """GET the one-call manager briefing."""
    query = urlencode(
        {"decisions_per_sleeve": str(decisions_per_sleeve), "journal_limit": str(journal_limit)}
    )
    return _get(f"{base_url}{_PREFIX}/{portfolio_id}/briefing?{query}", "manager briefing")


def submit_proposal(
    base_url: str, portfolio_id: UUID, request: ProposalSubmitRequest
) -> JsonObject:
    """POST one manager proposal."""
    return _mutate(
        "POST",
        f"{base_url}{_PREFIX}/{portfolio_id}/proposals",
        request.model_dump(mode="json", exclude_none=True),
        "proposal",
    )


def list_proposals(
    base_url: str,
    portfolio_id: UUID,
    *,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> JsonObject:
    """GET proposals newest first."""
    query = {"limit": str(limit)}
    if status is not None:
        query["status"] = status
    if cursor is not None:
        query["cursor"] = cursor
    return _get(f"{base_url}{_PREFIX}/{portfolio_id}/proposals?{urlencode(query)}", "proposals")


def show_proposal(base_url: str, portfolio_id: UUID, proposal_id: UUID) -> JsonObject:
    """GET one proposal."""
    return _get(f"{base_url}{_PREFIX}/{portfolio_id}/proposals/{proposal_id}", "proposal")


def decide_proposal(
    base_url: str,
    portfolio_id: UUID,
    proposal_id: UUID,
    *,
    decision: str,
    request: ProposalDecisionRequest,
) -> JsonObject:
    """POST approve or decline for one pending proposal."""
    return _mutate(
        "POST",
        f"{base_url}{_PREFIX}/{portfolio_id}/proposals/{proposal_id}/{decision}",
        request.model_dump(mode="json", exclude_none=True),
        "proposal",
    )


def _get(url: str, what: str) -> JsonObject:
    """GET one JSON object."""
    return _object(request_json(method="GET", url=url), what)


def _mutate(
    method: str, url: str, payload: object | None, what: str, *, timeout: float = 30.0
) -> JsonObject:
    """Send one installation-authenticated mutation and return its JSON object."""
    return _object(
        request_mutation_json(method=method, url=url, payload=payload, timeout=timeout), what
    )


def _object(value: object, what: str) -> JsonObject:
    """Require a JSON object response."""
    if not isinstance(value, dict):
        raise AgentHttpError(f"ThyTrader API returned an unexpected {what} payload.")
    return {str(key): item for key, item in value.items()}
