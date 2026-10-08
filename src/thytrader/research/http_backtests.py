"""Research HTTP client for backtests.

Submit one backtest, describe the backtest model, and read results, exports,
per-bar explanations, and promotion evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from thytrader.agent_http import AgentHttpError, request_json, request_mutation_json
from thytrader.market_data.models import published_execution_timeframe
from thytrader.research.http_common import (
    _SYNC_SUBMIT_TIMEOUT_SECONDS,
    _as_object,
    _encode,
    _sync_wait_elapsed,
)
from thytrader.research.http_strategies import _strategy_source
from thytrader.research.mutation import ResearchMutationError

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.submission import BacktestStartRequest


def submit_backtest(
    base_url: str,
    request: BacktestStartRequest,
    *,
    async_submission: bool = False,
) -> str:
    """POST one backtest start; the server snapshots the strategy's current rules."""
    url = f"{base_url}/api/v1/backtests"
    if async_submission:
        url = f"{url}?async=true"
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=url,
            payload=request.model_dump(mode="json"),
            timeout=_SYNC_SUBMIT_TIMEOUT_SECONDS,
        ),
        "submit-backtest response",
    )
    if not async_submission and "job_id" in body:
        return _sync_wait_elapsed(body)
    if async_submission:
        keys: tuple[str, ...] = (
            "job_id",
            "status",
            "strategy_id",
            "strategy_fingerprint",
            "bound_datasets",
        )
    else:
        keys = (
            "run_fingerprint",
            "result_fingerprint",
            "strategy_id",
            "strategy_fingerprint",
            "bound_datasets",
        )
    return _encode({key: body.get(key) for key in keys})


def backtest_model(base_url: str) -> str:
    """Fetch the unified backtest model's identity and fill assumptions."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/backtest-model"),
        "backtest model",
    )
    return _encode(body)


def list_results(
    base_url: str,
    strategy_fingerprint: str | None,
    limit: int,
    cursor: str | None = None,
    strategy_id: UUID | None = None,
) -> str:
    """List bounded immutable result summaries (by strategy or by exact snapshot)."""
    url = f"{base_url}/api/v1/backtests?limit={limit}"
    if strategy_fingerprint:
        url = f"{url}&strategy_fingerprint={strategy_fingerprint}"
    if strategy_id is not None:
        url = f"{url}&strategy_id={strategy_id}"
    if cursor:
        url = f"{url}&cursor={cursor}"
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
                "strategy_id": row.get("strategy_id"),
                "dataset_fingerprint": row.get("dataset_fingerprint"),
                "published_at": row.get("published_at"),
                "trade_count": summary.get("trade_count"),
                "total_net_pnl": summary.get("total_net_pnl"),
                "total_return_fraction": summary.get("total_return_fraction"),
                **window_fields(row.get("window")),
            }
        )
    return _encode(
        {
            "results": results,
            "limit": body.get("limit", limit),
            "returned": body.get("returned", len(results)),
            "has_more": body.get("has_more", False),
            "next_cursor": body.get("next_cursor"),
        }
    )


def _published_clock_and_quote(source: dict[str, object]) -> tuple[str, str]:
    """Return (timeframe, quote currency) copied from a published strategy."""
    strategy = _as_object(source.get("strategy"), "published strategy")
    timeframe = strategy.get("timeframe")
    instrument = strategy.get("instrument")
    quote = instrument.get("quote_currency") if isinstance(instrument, dict) else None
    if quote not in {"USD", "USDC", "USDT"}:
        raise ResearchMutationError("Published strategy quote currency was not USD, USDC, or USDT.")
    clock = published_execution_timeframe(timeframe) if isinstance(timeframe, str) else "1h"
    return clock, str(quote)


def show_evidence(base_url: str, strategy_fingerprint: str) -> str:
    """Show IS vs OOS vs sweep vs paper vs live evidence for one strategy."""
    body = _as_object(
        request_json(
            method="GET",
            url=(
                f"{base_url}/api/v1/research/promotion-evidence"
                f"?strategy_fingerprint={strategy_fingerprint}"
            ),
        ),
        "promotion evidence",
    )
    return _encode(body)


def export_results(
    base_url: str, *, limit: int = 50, cursor: str | None = None, strategy_id: UUID | None = None
) -> str:
    """Read a bounded page with summary, costs, window, metrics and diagnostics."""
    query = {"limit": str(limit)}
    if cursor is not None:
        query["cursor"] = cursor
    if strategy_id is not None:
        query["strategy_id"] = str(strategy_id)
    return _encode(
        _as_object(
            request_json(
                method="GET", url=f"{base_url}/api/v1/backtests/export?{urlencode(query)}"
            ),
            "research export",
        )
    )


def explain_bars(
    base_url: str,
    result_fingerprint: str,
    *,
    limit: int,
    cursor: str | None,
) -> str:
    """GET one bounded page of per-bar explanations for an immutable result."""
    query = {"limit": str(limit)}
    if cursor:
        query["cursor"] = cursor
    body = request_json(
        method="GET",
        url=(
            f"{base_url}/api/v1/backtests/{result_fingerprint}/bar-explanations?{urlencode(query)}"
        ),
    )
    return _encode(_as_object(body, "bar explanations"))


def show_result(base_url: str, result_fingerprint: str) -> str:
    """Show one result summary without dumping the full trade ledger."""
    body = _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/backtests/{result_fingerprint}?detail=summary",
        ),
        "backtest detail",
    )
    if "result" in body:
        result = _as_object(body.get("result"), "backtest result")
        strategy_fingerprint = result.get("strategy_fingerprint")
        run_fingerprint = result.get("run_fingerprint")
        dataset_fingerprint = result.get("dataset_fingerprint")
        summary = result.get("summary")
    else:
        strategy_fingerprint = body.get("strategy_fingerprint")
        run_fingerprint = body.get("run_fingerprint")
        dataset_fingerprint = body.get("dataset_fingerprint")
        summary = body.get("summary")
    timeframe = "1h"
    currency: str | None = None
    if isinstance(strategy_fingerprint, str):
        try:
            source = _strategy_source(base_url, strategy_fingerprint)
        except AgentHttpError, ResearchMutationError:
            pass
        else:
            timeframe, currency = _published_clock_and_quote(source)
    return _encode(
        {
            "result_fingerprint": body.get("result_fingerprint"),
            "run_fingerprint": run_fingerprint,
            "strategy_fingerprint": strategy_fingerprint,
            "dataset_fingerprint": dataset_fingerprint,
            "mode": "backtest",
            "timeframe": timeframe,
            "currency": currency,
            "summary": summary,
            "window": body.get("window"),
            "costs": body.get("costs"),
            "metrics": body.get("metrics"),
            "cost_attribution": body.get("cost_attribution"),
            "diagnostics": body.get("diagnostics"),
            "verification_scope": body.get("verification_scope"),
            "warnings": body.get("warnings", []),
        }
    )


def window_fields(window: object) -> dict[str, object]:
    """Flatten one result's evaluated window for listing rows (null when unknown).

    Results with omitted bounds start after each strategy's own warmup, so rows from
    different strategies can cover different bars; compare them only when
    ``evaluation_start`` and ``evaluation_end`` match (ADR 0094).
    """
    known = window if isinstance(window, dict) else {}
    return {
        "evaluation_start": known.get("evaluation_start"),
        "evaluation_end": known.get("evaluation_end"),
        "warmup_bars": known.get("warmup_bars"),
        "evaluation_bars": known.get("evaluation_bars"),
    }
