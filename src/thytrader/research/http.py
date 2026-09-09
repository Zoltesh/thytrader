"""HTTP research mutations against existing strategy and backtest routes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thytrader.agent_http import request_json
from thytrader.research.mutation import ResearchMutationError

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.submission import BacktestSubmissionRequest
    from thytrader.strategies.models import StrategyDefinition


def create_draft(base_url: str) -> str:
    """POST the conservative reference draft through the strategies API."""
    body = _as_object(
        request_json(method="POST", url=f"{base_url}/api/v1/strategies"),
        "create-draft response",
    )
    strategy = _as_object(body.get("strategy"), "created strategy")
    return _encode(
        {
            "strategy_id": _as_str(strategy.get("strategy_id"), "strategy_id"),
            "revision": body.get("revision"),
            "version": strategy.get("version"),
            "name": strategy.get("name"),
        }
    )


def save_draft(base_url: str, definition: StrategyDefinition, revision: int) -> str:
    """PUT one draft JSON document through the strategies API."""
    strategy_id = definition.strategy_id
    version = definition.version
    body = _as_object(
        request_json(
            method="PUT",
            url=f"{base_url}/api/v1/strategies/{strategy_id}/versions/{version}",
            payload={"strategy": definition.model_dump(mode="json"), "revision": revision},
        ),
        "save-draft response",
    )
    strategy = _as_object(body.get("strategy"), "saved strategy")
    return _encode(
        {
            "strategy_id": _as_str(strategy.get("strategy_id"), "strategy_id"),
            "revision": body.get("revision"),
            "version": strategy.get("version"),
        }
    )


def publish(base_url: str, strategy_id: UUID) -> str:
    """Load the matching draft over HTTP and publish it immutably."""
    listing = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/strategies"),
        "strategy list",
    )
    entry = _draft_entry(listing, str(strategy_id))
    version = entry.get("latest_version")
    if not isinstance(version, int):
        raise ResearchMutationError("Strategy draft was not found.")
    draft = _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/strategies/{strategy_id}/versions/{version}",
        ),
        "draft version",
    )
    published = _as_object(
        request_json(
            method="POST",
            url=f"{base_url}/api/v1/strategies/{strategy_id}/publish",
            payload={
                "strategy": draft.get("strategy"),
                "revision": draft.get("revision"),
            },
        ),
        "publish response",
    )
    strategy = _as_object(published.get("strategy"), "published strategy")
    return _encode(
        {
            "strategy_id": _as_str(strategy.get("strategy_id"), "strategy_id"),
            "strategy_fingerprint": published.get("strategy_fingerprint"),
            "version": strategy.get("version"),
        }
    )


def submit_backtest(base_url: str, request: BacktestSubmissionRequest) -> str:
    """POST one idempotent research run through the backtests API."""
    body = _as_object(
        request_json(
            method="POST",
            url=f"{base_url}/api/v1/backtests",
            payload=request.model_dump(mode="json"),
        ),
        "submit-backtest response",
    )
    return _encode(
        {
            "run_fingerprint": body.get("run_fingerprint"),
            "result_fingerprint": body.get("result_fingerprint"),
        }
    )


def list_results(base_url: str, strategy_fingerprint: str | None, limit: int) -> str:
    """List bounded immutable result summaries."""
    url = f"{base_url}/api/v1/backtests?limit={limit}"
    if strategy_fingerprint:
        url = f"{url}&strategy_fingerprint={strategy_fingerprint}"
    body = _as_object(request_json(method="GET", url=url), "backtest list")
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ResearchMutationError("Backtest list was not a JSON array.")
    results: list[dict[str, object]] = []
    for item in entries:
        row = _as_object(item, "backtest summary")
        summary = _as_object(row.get("summary"), "backtest summary payload")
        results.append(
            {
                "result_fingerprint": row.get("result_fingerprint"),
                "run_fingerprint": row.get("run_fingerprint"),
                "strategy_fingerprint": row.get("strategy_fingerprint"),
                "dataset_fingerprint": row.get("dataset_fingerprint"),
                "engine_contract_version": row.get("engine_contract_version"),
                "published_at": row.get("published_at"),
                "trade_count": summary.get("trade_count"),
                "total_net_pnl": summary.get("total_net_pnl"),
            }
        )
    return _encode({"results": results})


def show_result(base_url: str, result_fingerprint: str) -> str:
    """Show one result summary without dumping the full trade ledger."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/backtests/{result_fingerprint}"),
        "backtest detail",
    )
    result = _as_object(body.get("result"), "backtest result")
    return _encode(
        {
            "result_fingerprint": body.get("result_fingerprint"),
            "run_fingerprint": result.get("run_fingerprint"),
            "strategy_fingerprint": result.get("strategy_fingerprint"),
            "dataset_fingerprint": result.get("dataset_fingerprint"),
            "engine_contract_version": result.get("engine_contract_version"),
            "mode": "backtest",
            "timeframe": "1h",
            "currency": "USD",
            "summary": result.get("summary"),
        }
    )


def _draft_entry(listing: dict[str, object], strategy_id: str) -> dict[str, object]:
    """Find the library row for one draft identity."""
    strategies = listing.get("strategies")
    if not isinstance(strategies, list):
        raise ResearchMutationError("Strategy list was not a JSON array.")
    for item in strategies:
        entry = _as_object(item, "strategy library entry")
        if entry.get("strategy_id") == strategy_id and entry.get("status") == "draft":
            return entry
    raise ResearchMutationError("Strategy draft was not found.")


def _as_object(value: object, what: str) -> dict[str, object]:
    """Require a JSON object at a system boundary."""
    if not isinstance(value, dict):
        raise ResearchMutationError(f"{what} was not a JSON object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ResearchMutationError(f"{what} had a non-string key.")
        result[key] = item
    return result


def _as_str(value: object, name: str) -> str:
    """Require a string identity field."""
    if not isinstance(value, str) or not value:
        raise ResearchMutationError(f"Missing {name}.")
    return value


def _encode(payload: object) -> str:
    """Render stable JSON for agent consumption."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
