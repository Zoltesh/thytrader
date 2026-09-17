"""HTTP research mutations against existing strategy and backtest routes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.agent_http import AgentHttpError, request_json, request_mutation_json
from thytrader.memory.models import ExperientialModel
from thytrader.research.mutation import ResearchMutationError

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.submission import BacktestSubmissionRequest
    from thytrader.research.studies import ResearchStudyRequest
    from thytrader.strategies.models import StrategyDefinition


def create_draft(
    base_url: str,
    *,
    product_id: str = "BTC-USD",
    timeframe: str = "1h",
    template: str = "ema-trend",
    experiential_model_id: str | None = None,
) -> str:
    """POST a research template draft through the strategies API.

    Optional ``experiential_model_id`` is fail-closed HTTP-only advisory input.
    It never changes published strategy semantics or places orders.
    """
    advisory = (
        _experiential_advisory_fields(base_url, experiential_model_id)
        if experiential_model_id is not None
        else {}
    )
    url = f"{base_url}/api/v1/strategies"
    query: list[str] = []
    if product_id != "BTC-USD":
        query.append(f"product_id={product_id}")
    if timeframe != "1h":
        query.append(f"timeframe={timeframe}")
    if template != "ema-trend":
        query.append(f"template={template}")
    if query:
        url = f"{url}?{'&'.join(query)}"
    body = _as_object(
        request_mutation_json(method="POST", url=url),
        "create-draft response",
    )
    strategy = _as_object(body.get("strategy"), "created strategy")
    payload: dict[str, object] = {
        "strategy_id": _as_str(strategy.get("strategy_id"), "strategy_id"),
        "revision": body.get("revision"),
        "version": strategy.get("version"),
        "name": strategy.get("name"),
    }
    payload.update(advisory)
    return _encode(payload)


def save_draft(base_url: str, definition: StrategyDefinition, revision: int) -> str:
    """PUT one draft JSON document through the strategies API."""
    strategy_id = definition.strategy_id
    version = definition.version
    body = _as_object(
        request_mutation_json(
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


def import_draft(base_url: str, definition: StrategyDefinition) -> str:
    """POST one new custom strategy document through the strategies import API."""
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/strategies/import",
            payload={"strategy": definition.model_dump(mode="json")},
        ),
        "import-draft response",
    )
    strategy = _as_object(body.get("strategy"), "imported strategy")
    return _encode(
        {
            "strategy_id": _as_str(strategy.get("strategy_id"), "strategy_id"),
            "revision": body.get("revision"),
            "version": strategy.get("version"),
            "name": strategy.get("name"),
            "summary": body.get("summary"),
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
        request_mutation_json(
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


def submit_backtest(
    base_url: str,
    request: BacktestSubmissionRequest,
    *,
    async_submission: bool = False,
) -> str:
    """POST one idempotent research run through the backtests API."""
    url = f"{base_url}/api/v1/backtests"
    if async_submission:
        url = f"{url}?async=true"
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=url,
            payload=request.model_dump(mode="json"),
        ),
        "submit-backtest response",
    )
    if async_submission:
        return _encode({"job_id": body.get("job_id"), "status": body.get("status")})
    return _encode(
        {
            "run_fingerprint": body.get("run_fingerprint"),
            "result_fingerprint": body.get("result_fingerprint"),
        }
    )


def show_backtest_job(base_url: str, job_id: str) -> str:
    """GET one async backtest job status."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/backtests/jobs/{job_id}"),
        "backtest job",
    )
    return _encode(body)


def list_templates(base_url: str) -> str:
    """List fail-closed draft templates."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/templates"),
        "template list",
    )
    return _encode(body)


def engine_support(base_url: str) -> str:
    """Fetch the V1-V4 engine-support matrix."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/engine-support"),
        "engine-support matrix",
    )
    return _encode(body)


def plan_study(base_url: str, request: ResearchStudyRequest) -> str:
    """POST a study window plan without submitting child backtests."""
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/studies/plan",
            payload=request.model_dump(mode="json"),
        ),
        "plan-study response",
    )
    return _encode(body)


def submit_study(base_url: str, request: ResearchStudyRequest) -> str:
    """POST one composed research study through the research API."""
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/studies",
            payload=request.model_dump(mode="json"),
        ),
        "submit-study response",
    )
    return _encode(body)


def list_studies(base_url: str, kind: str | None, limit: int) -> str:
    """List persisted research-study catalog rows."""
    url = f"{base_url}/api/v1/research/studies?limit={limit}"
    if kind:
        url = f"{url}&kind={kind}"
    body = _as_object(request_json(method="GET", url=url), "study catalog")
    return _encode(body)


def show_study(base_url: str, study_fingerprint: str) -> str:
    """Show one persisted study summary (default HTTP projection)."""
    body = _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/research/studies/{study_fingerprint}",
        ),
        "study detail",
    )
    return _encode(body)


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
    strategy_fingerprint = result.get("strategy_fingerprint")
    timeframe = "1h"
    if isinstance(strategy_fingerprint, str):
        try:
            timeframe = _strategy_timeframe(base_url, strategy_fingerprint)
        except AgentHttpError, ResearchMutationError:
            timeframe = "1h"
    return _encode(
        {
            "result_fingerprint": body.get("result_fingerprint"),
            "run_fingerprint": result.get("run_fingerprint"),
            "strategy_fingerprint": strategy_fingerprint,
            "dataset_fingerprint": result.get("dataset_fingerprint"),
            "engine_contract_version": result.get("engine_contract_version"),
            "mode": "backtest",
            "timeframe": timeframe,
            "currency": "USD",
            "summary": result.get("summary"),
        }
    )


def _experiential_advisory_fields(base_url: str, model_id: str) -> dict[str, object]:
    """Load one trained model and return advisory fields for the draft JSON."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/memory/models/{model_id}"),
        "experiential model",
    )
    try:
        model = ExperientialModel.model_validate(body)
    except ValidationError as error:
        raise ResearchMutationError("Experiential model document failed validation.") from error
    if str(model.id) != model_id:
        raise ResearchMutationError("Experiential model id did not match the request.")
    return {
        "experiential_model_id": str(model.id),
        "experiential_fingerprint": model.fingerprint,
        "experiential_advisory": model.advisory.model_dump(mode="json"),
    }


def _strategy_timeframe(base_url: str, strategy_fingerprint: str) -> str:
    """Read the published strategy timeframe without inventing unsupported intervals."""
    source = _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/strategies/source/{strategy_fingerprint}",
        ),
        "strategy source",
    )
    strategy = _as_object(source.get("strategy"), "published strategy")
    timeframe = strategy.get("timeframe")
    if timeframe == "5m":
        return "5m"
    return "1h"


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
