"""HTTP research mutations against existing strategy and backtest routes."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode
from uuid import UUID

from pydantic import ValidationError

from thytrader.agent_http import AgentHttpError, request_json, request_mutation_json
from thytrader.execution.economics import EconomicPreflightRequest
from thytrader.market_data.models import published_execution_timeframe
from thytrader.memory.models import ExperientialModel
from thytrader.ops_contract import STALE_IMAGE_REBUILD
from thytrader.research.campaigns import CampaignStart
from thytrader.research.mutation import ResearchMutationError
from thytrader.strategies.library import StrategyOrigin

if TYPE_CHECKING:
    from thytrader.backtest.submission import BacktestStartRequest
    from thytrader.research.study_start import ResearchStudyStartRequest
    from thytrader.strategies.library import StrategyDocument

# A synchronous submit waits server-side for at most ``research_sync_wait_seconds``
# (default 25 s) and then answers 202 with the job, so the client allows a margin.
_SYNC_SUBMIT_TIMEOUT_SECONDS = 60.0
# The API plans the study and auto-binds its datasets before it queues the job, which can
# take well over 5 s on a large sweep; the study still persists, so a short client timeout
# only turned a success into an alarming "ambiguous submit" error.
ASYNC_SUBMIT_TIMEOUT_SECONDS = 30.0
_MAX_TAG_PAGES = 100
_BULK_COUNTS = ("deleted", "would_delete", "blocked", "not_found", "failed")


def create_strategy(
    base_url: str,
    *,
    product_id: str = "BTC-USD",
    timeframe: str = "1h",
    template: str = "ema-trend",
    experiential_model_id: str | None = None,
) -> str:
    """POST one research template strategy through the strategies API.

    Optional ``experiential_model_id`` is fail-closed HTTP-only advisory input.
    It never changes strategy semantics or places orders.
    """
    advisory = (
        _experiential_advisory_fields(base_url, experiential_model_id)
        if experiential_model_id is not None
        else {}
    )
    query = urlencode({"product_id": product_id, "timeframe": timeframe, "template": template})
    body = _as_object(
        request_mutation_json(method="POST", url=f"{base_url}/api/v1/strategies?{query}"),
        "create-strategy response",
    )
    payload = _strategy_digest(body)
    payload.update(advisory)
    return _encode(payload)


def show_strategy(base_url: str, strategy_id: UUID) -> str:
    """GET one strategy's current document, validation, and fingerprint."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/strategies/{strategy_id}"),
        "strategy",
    )
    return _encode(body)


def save_strategy(
    base_url: str, strategy_id: UUID, document: StrategyDocument, revision: int
) -> str:
    """PUT one document in place; a stale revision fails with strategy_revision_conflict."""
    body = _as_object(
        request_mutation_json(
            method="PUT",
            url=f"{base_url}/api/v1/strategies/{strategy_id}",
            payload={"document": document, "revision": revision},
        ),
        "save-strategy response",
    )
    return _encode(_strategy_digest(body))


def import_strategy(base_url: str, document: StrategyDocument) -> str:
    """POST one JSON document as a new strategy (fresh identity)."""
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/strategies/import",
            payload={"document": document},
        ),
        "import-strategy response",
    )
    return _encode(_strategy_digest(body))


def clone_strategy(base_url: str, strategy_id: UUID, *, name: str | None = None) -> str:
    """POST one clone of a strategy into a new identity, optionally named in the same call."""
    body = _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/strategies/{strategy_id}/clone",
            payload=None if name is None else {"name": name},
        ),
        "clone-strategy response",
    )
    return _encode(_strategy_digest(body))


def delete_strategy(base_url: str, strategy_id: UUID) -> str:
    """DELETE one strategy; running or paused bots block it (409)."""
    body = _as_object(
        request_mutation_json(
            method="DELETE",
            url=f"{base_url}/api/v1/strategies/{strategy_id}",
            timeout=30.0,
        ),
        "delete-strategy response",
    )
    return _encode(body)


def bulk_delete_strategies(base_url: str, strategy_ids: tuple[UUID, ...], *, dry_run: bool) -> str:
    """POST one bulk delete (or dry run) and return per-strategy results."""
    return _encode(_bulk_delete_batch(base_url, strategy_ids, dry_run=dry_run))


def _bulk_delete_batch(
    base_url: str, strategy_ids: tuple[UUID, ...], *, dry_run: bool
) -> dict[str, object]:
    """POST one bounded bulk delete (at most 100 ids) and return its body."""
    return _as_object(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/strategies/bulk-delete",
            payload={
                "strategy_ids": [str(item) for item in strategy_ids],
                "confirm": not dry_run,
                "dry_run": dry_run,
            },
            timeout=60.0,
        ),
        "bulk-delete response",
    )


def tagged_strategy_ids(base_url: str, tag: str) -> tuple[UUID, ...]:
    """Every strategy id whose ``metadata.tags`` include ``tag``, across all library pages."""
    found: list[UUID] = []
    cursor: str | None = None
    for _page in range(_MAX_TAG_PAGES):
        body = _library_page(base_url, limit=100, cursor=cursor, tag=tag)
        rows = body.get("strategies")
        if not isinstance(rows, list):
            raise ResearchMutationError("Strategy library was not a JSON array.")
        for item in rows:
            row = _as_object(item, "library row")
            tags = row.get("tags")
            if not isinstance(tags, list) or tag not in tags:
                # Fail closed: an API that ignored ``tag`` would list every strategy.
                raise ResearchMutationError(
                    f"The API listed a strategy without tag {tag!r}; nothing was deleted. "
                    f"{STALE_IMAGE_REBUILD}"
                )
            found.append(UUID(_as_str(row.get("strategy_id"), "strategy_id")))
        next_cursor = body.get("next_cursor")
        if not body.get("has_more") or not isinstance(next_cursor, str):
            return tuple(dict.fromkeys(found))
        cursor = next_cursor
    raise ResearchMutationError(
        f"More than {_MAX_TAG_PAGES * 100} strategies carry tag {tag!r}; narrow the tag."
    )


def bulk_delete_tagged(base_url: str, tag: str, *, dry_run: bool) -> str:
    """Delete (or preview deleting) every strategy tagged ``tag`` in batches of 100.

    Each batch goes through ``POST /api/v1/strategies/bulk-delete``, so the server's
    per-strategy safety holds: running or paused bots block their strategy and live
    ledgers are kept. Results from every batch are merged into one report.
    """
    identities = tagged_strategy_ids(base_url, tag)
    merged: dict[str, object] = {
        "dry_run": dry_run,
        "tag": tag,
        "matched": len(identities),
        "results": [],
    }
    counts = dict.fromkeys(_BULK_COUNTS, 0)
    results: list[object] = []
    for start in range(0, len(identities), 100):
        body = _bulk_delete_batch(base_url, identities[start : start + 100], dry_run=dry_run)
        batch = body.get("results")
        results.extend(batch if isinstance(batch, list) else [])
        for key in _BULK_COUNTS:
            value = body.get(key)
            counts[key] += value if isinstance(value, int) else 0
    merged["results"] = results
    merged.update(counts)
    return _encode(merged)


def show_snapshot(base_url: str, strategy_fingerprint: str) -> str:
    """GET one strategy snapshot (the exact rules a run or bot used) and its owner."""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", strategy_fingerprint) is None:
        raise ResearchMutationError("--strategy-fingerprint must match sha256:<64 hex characters>.")
    return _encode(_strategy_source(base_url, strategy_fingerprint))


def _library_page(
    base_url: str,
    *,
    limit: int,
    cursor: str | None,
    tag: str | None,
    origin: StrategyOrigin = StrategyOrigin.ALL,
) -> dict[str, object]:
    """GET one library page (optionally only strategies tagged ``tag`` or of ``origin``)."""
    query: dict[str, str] = {"limit": str(limit)}
    if cursor:
        query["cursor"] = cursor
    if tag is not None:
        query["tag"] = tag
    if origin is not StrategyOrigin.ALL:
        query["origin"] = origin.value
    return _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/strategies?{urlencode(query)}"),
        "strategy list",
    )


def list_strategies(
    base_url: str,
    *,
    limit: int = 50,
    cursor: str | None = None,
    tag: str | None = None,
    origin: StrategyOrigin = StrategyOrigin.ALL,
) -> str:
    """List one page of the strategy library (newest updated first), by tag or origin."""
    body = _library_page(base_url, limit=limit, cursor=cursor, tag=tag, origin=origin)
    strategies = body.get("strategies")
    if not isinstance(strategies, list):
        raise ResearchMutationError("Strategy library was not a JSON array.")
    rows = [
        {
            key: row.get(key)
            for key in (
                "strategy_id",
                "name",
                "product_id",
                "timeframe",
                "revision",
                "valid",
                "tags",
                "current_fingerprint",
                "paper_live",
                "active_deployment_count",
                "updated_at",
            )
        }
        for row in (_as_object(item, "strategy library row") for item in strategies)
    ]
    return _encode(
        {
            "strategies": rows,
            "limit": body.get("limit", limit),
            "returned": body.get("returned", len(rows)),
            "total": body.get("total"),
            "has_more": body.get("has_more", False),
            "next_cursor": body.get("next_cursor"),
        }
    )


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


def backtest_model(base_url: str) -> str:
    """Fetch the unified backtest model's identity and fill assumptions."""
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/backtest-model"),
        "backtest model",
    )
    return _encode(body)


def plan_study(
    base_url: str,
    request: ResearchStudyStartRequest,
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
    request: ResearchStudyStartRequest,
    *,
    async_submission: bool = False,
    timeout_seconds: float | None = None,
) -> str:
    """POST one composed research study through the research API.

    ``timeout_seconds`` overrides the client wait: 30 s for an async submit (the API
    pins snapshots and dataset bindings before queueing) and 60 s for a synchronous one.
    """
    url = f"{base_url}/api/v1/research/studies"
    if async_submission:
        url = f"{url}?async=true"
    try:
        body = _as_object(
            request_mutation_json(
                method="POST",
                url=url,
                payload=request.model_dump(mode="json"),
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else ASYNC_SUBMIT_TIMEOUT_SECONDS
                    if async_submission
                    else _SYNC_SUBMIT_TIMEOUT_SECONDS
                ),
            ),
            "submit-study response",
        )
    except AgentHttpError as error:
        raise _ambiguous_study_error(request, error) from error
    if not async_submission and "job_id" in body:
        return _sync_wait_elapsed(body)
    if async_submission:
        return _encode(
            {
                key: body.get(key)
                for key in (
                    "job_id",
                    "kind",
                    "status",
                    "strategy_id",
                    "strategy_fingerprint",
                    "bound_datasets",
                    "evaluation_start",
                    "evaluation_end",
                )
            }
        )
    return _encode(body)


def _sync_wait_elapsed(body: dict[str, object]) -> str:
    """Report a synchronous submit the research worker had not finished (HTTP 202).

    The API waited ``sync_wait_seconds`` and handed back the queued or running job
    (ADR 0092); the job keeps running. Poll it rather than submitting again.
    """
    keys = (
        "job_id",
        "kind",
        "status",
        "strategy_id",
        "strategy_fingerprint",
        "bound_datasets",
        "evaluation_start",
        "evaluation_end",
        "sync_wait_seconds",
    )
    payload: dict[str, object] = {key: body.get(key) for key in keys if key in body}
    payload["next_action"] = (
        f"thytrader-research show-research-job --job-id {body.get('job_id')} "
        "(the research worker is still running it; do not resubmit)"
    )
    return _encode(payload)


def _ambiguous_study_error(
    request: ResearchStudyStartRequest,
    error: AgentHttpError,
) -> AgentHttpError:
    """Convert a study-submission failure into an actionable operator error.

    A definitive rejection — a 4xx status other than 408 — proves the server
    saw and refused the request, so nothing was persisted. Ambiguous failures
    (client timeout, a dropped connection, unreachable transport, 408, or any
    5xx after this write-risk POST) name the readback command, because the
    study may already be persisted under the strategy the server snapshotted.
    """
    message = str(error)
    status = error.status
    if status is not None:
        ambiguous = status == 408 or status >= 500
    else:
        lowered = message.lower()
        ambiguous = (
            error.timed_out or error.dropped or "timed out" in lowered or "unreachable" in lowered
        )
    identities = request.strategy_ids()
    primary = identities[0] if identities else None
    readback = (
        f"thytrader-research list-studies --strategy-id {primary} --limit 20"
        if primary is not None
        else "thytrader-research list-studies --limit 50"
    )
    if ambiguous:
        return AgentHttpError(
            f"{message} Submit-state is ambiguous: the study may already be "
            f"persisted. Read back before retrying: {readback}",
            status=status,
            timed_out=error.timed_out,
            dropped=error.dropped,
            code=error.code,
        )
    return AgentHttpError(
        f"{message} (Verify with: {readback})",
        status=status,
        timed_out=error.timed_out,
        dropped=error.dropped,
        code=error.code,
    )


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


def list_research_jobs(base_url: str, strategy_id: UUID, limit: int) -> str:
    """GET one strategy's newest research jobs (sync and async, every status)."""
    query = urlencode({"strategy_id": str(strategy_id), "limit": limit})
    body = _as_object(
        request_json(method="GET", url=f"{base_url}/api/v1/research/jobs?{query}"),
        "research jobs",
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


def list_studies(
    base_url: str, kind: str | None, limit: int, strategy_id: UUID | None = None
) -> str:
    """List persisted research-study catalog rows (optionally for one strategy)."""
    url = f"{base_url}/api/v1/research/studies?limit={limit}"
    if kind:
        url = f"{url}&kind={kind}"
    if strategy_id is not None:
        url = f"{url}&strategy_id={strategy_id}"
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


def _strategy_source(base_url: str, strategy_fingerprint: str) -> dict[str, object]:
    """Load one strategy snapshot document."""
    return _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/strategies/snapshots/{strategy_fingerprint}",
        ),
        "strategy snapshot",
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


def _strategy_digest(body: dict[str, object]) -> dict[str, object]:
    """Summarize one StrategyResponse without its full document.

    ``validation`` has exactly the shape ``show-strategy`` returns (ADR 0094). The
    top-level ``valid`` / ``issues`` / ``warnings`` copies are deprecated and kept
    for one release so existing parsers keep working.
    """
    validation = _as_object(body.get("validation"), "strategy validation")
    nested: dict[str, object] = {
        "valid": validation.get("valid"),
        "issues": validation.get("issues", []),
        "warnings": validation.get("warnings", []),
    }
    return {
        "strategy_id": _as_str(body.get("strategy_id"), "strategy_id"),
        "name": body.get("name"),
        "revision": body.get("revision"),
        "validation": nested,
        "valid": nested["valid"],
        "issues": nested["issues"],
        "warnings": nested["warnings"],
        "current_fingerprint": body.get("current_fingerprint"),
        "summary": body.get("summary"),
    }


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


def create_campaign(base_url: str, document: object) -> str:
    """Validate and freeze a campaign through the separately confirmed research lane."""
    request = CampaignStart.model_validate(document)
    return _encode(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/campaigns",
            payload=request.model_dump(mode="json"),
        )
    )


def economics(base_url: str, document: object) -> str:
    """Calculate read-only economics; all POSTs still cross the installation boundary."""
    request = EconomicPreflightRequest.model_validate(document)
    return _encode(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/economics",
            payload=request.model_dump(mode="json"),
        )
    )


def list_campaigns(base_url: str, *, limit: int = 20) -> str:
    """Read a bounded campaign page."""
    return _encode(
        request_json(method="GET", url=f"{base_url}/api/v1/research/campaigns?limit={limit}")
    )


def show_campaign(base_url: str, campaign_id: UUID) -> str:
    """Read a frozen manifest, costs, deadlines, sample gates, and child evidence."""
    return _encode(
        request_json(method="GET", url=f"{base_url}/api/v1/research/campaigns/{campaign_id}")
    )


def refresh_campaign(base_url: str, campaign_id: UUID) -> str:
    """Advance only the research jobs authorized by the frozen manifest."""
    return _encode(
        request_mutation_json(
            method="POST", url=f"{base_url}/api/v1/research/campaigns/{campaign_id}/refresh"
        )
    )
