"""HTTP research mutations against existing strategy and backtest routes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from thytrader.agent_http import AgentHttpError, request_json, request_mutation_json
from thytrader.market_data.models import published_execution_timeframe
from thytrader.memory.models import ExperientialModel
from thytrader.research.mutation import ResearchMutationError
from thytrader.research.studies import request_fingerprint

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
    return show_research_job(base_url, job_id)


def list_templates(base_url: str) -> str:
    """List fail-closed draft templates."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/templates"),
        "template list",
    )
    return _encode(body)


def show_template(base_url: str, template_id: str) -> str:
    """Show one template's defaults, indicator ids, and sweepable axes."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/templates/{template_id}"),
        "template detail",
    )
    return _encode(body)


def engine_support(base_url: str) -> str:
    """Fetch the V1-V4 engine-support matrix."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/engine-support"),
        "engine-support matrix",
    )
    return _encode(body)


def plan_study(
    base_url: str,
    request: ResearchStudyRequest,
    *,
    detail: Literal["summary", "full"] = "summary",
) -> str:
    """POST a study window plan without submitting child backtests."""
    url = f"{base_url}/api/v1/research/studies/plan"
    if detail == "full":
        url = f"{url}?detail=full"
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=url,
            payload=request.model_dump(mode="json"),
            timeout=30.0,
        ),
        "plan-study response",
    )
    return _encode(body)


def submit_study(
    base_url: str,
    request: ResearchStudyRequest,
    *,
    async_submission: bool = False,
) -> str:
    """POST one composed research study through the research API."""
    url = f"{base_url}/api/v1/research/studies"
    if async_submission:
        url = f"{url}?async=true"
    try:
        body = _as_object(
            request_mutation_json(
                method="POST",
                url=url,
                payload=request.model_dump(mode="json"),
                timeout=5.0,
            ),
            "submit-study response",
        )
    except AgentHttpError as error:
        raise _ambiguous_study_error(request, error) from error
    if async_submission:
        return _encode(
            {
                "job_id": body.get("job_id"),
                "kind": body.get("kind"),
                "status": body.get("status"),
            }
        )
    return _encode(body)


def _ambiguous_study_error(
    request: ResearchStudyRequest,
    error: AgentHttpError,
) -> AgentHttpError:
    """Convert a timed-out study submission into an actionable ambiguous-state error."""
    request_fp = request_fingerprint(request)
    message = str(error)
    ambiguous = "timed out" in message.lower() or "unreachable" in message.lower()
    readback = (
        "Submit-state is ambiguous: the study may already be persisted. "
        "Read back before retrying: "
        f"thytrader-research find-study-by-request --request-fingerprint {request_fp} "
        "(or `thytrader-research list-studies --limit 50`)"
    )
    if not ambiguous:
        return AgentHttpError(f"{message} ({readback})")
    return AgentHttpError(f"{message} {readback}")


def find_study_by_request(base_url: str, request_fingerprint: str) -> str:
    """Return the persisted study for one request fingerprint when it exists.

    The catalog route caps ``limit`` at 100, so scan bounded newest-first pages
    of 100 rows; a just-submitted study is recent and normally on page one.
    Rows persisted between page fetches can shift offsets (classic offset-
    pagination race); the scan then fails closed with an explicit not-found
    error, which is safe for a readback command.
    """
    prefix = f"{base_url}/api/v1/research/studies"
    for offset in range(0, 500, 100):
        body = _as_object(
            request_json(method="GET", url=f"{prefix}?limit=100&offset={offset}"),
            "study catalog",
        )
        studies = body.get("studies")
        if not isinstance(studies, list):
            raise ResearchMutationError("Study catalog was not a JSON array.")
        for item in studies:
            row = _as_object(item, "study catalog row")
            if row.get("request_fingerprint") == request_fingerprint:
                return _encode(row)
        if len(studies) < 100:
            break
    raise ResearchMutationError(
        f"No persisted study exists for request_fingerprint={request_fingerprint}."
    )


def show_research_job(base_url: str, job_id: str) -> str:
    """GET one async research job status."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/jobs/{job_id}"),
        "research job",
    )
    return _encode(body)


def cancel_research_job(base_url: str, job_id: str) -> str:
    """Cancel one queued or running research job."""
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/jobs/{job_id}/cancel",
            timeout=5.0,
        ),
        "cancel-research-job response",
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


def _strategy_source(base_url: str, strategy_fingerprint: str) -> dict[str, object]:
    """Load one published strategy source document."""
    return _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/strategies/source/{strategy_fingerprint}",
        ),
        "strategy source",
    )


def _published_clock_and_quote(source: dict[str, object]) -> tuple[str, str]:
    """Return (timeframe, quote currency) copied from a published strategy."""
    strategy = _as_object(source.get("strategy"), "published strategy")
    timeframe = strategy.get("timeframe")
    instrument = strategy.get("instrument")
    quote = instrument.get("quote_currency") if isinstance(instrument, dict) else None
    if quote not in {"USD", "USDC"}:
        raise ResearchMutationError("Published strategy quote currency was not USD or USDC.")
    clock = published_execution_timeframe(timeframe) if isinstance(timeframe, str) else "1h"
    return clock, str(quote)


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
        engine_contract_version = result.get("engine_contract_version")
        summary = result.get("summary")
    else:
        strategy_fingerprint = body.get("strategy_fingerprint")
        run_fingerprint = body.get("run_fingerprint")
        dataset_fingerprint = body.get("dataset_fingerprint")
        engine_contract_version = body.get("engine_contract_version")
        summary = body.get("summary")
    timeframe = "1h"
    currency = "USD"
    if isinstance(strategy_fingerprint, str):
        try:
            source = _strategy_source(base_url, strategy_fingerprint)
        except AgentHttpError, ResearchMutationError:
            timeframe = "1h"
            currency = "USD"
        else:
            timeframe, currency = _published_clock_and_quote(source)
    return _encode(
        {
            "result_fingerprint": body.get("result_fingerprint"),
            "run_fingerprint": run_fingerprint,
            "strategy_fingerprint": strategy_fingerprint,
            "dataset_fingerprint": dataset_fingerprint,
            "engine_contract_version": engine_contract_version,
            "mode": "backtest",
            "timeframe": timeframe,
            "currency": currency,
            "summary": summary,
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
