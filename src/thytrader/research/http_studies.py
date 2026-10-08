"""Research HTTP client for composed studies and the research job queue.

Plan and submit studies (naming the readback when a submit is ambiguous), read the
study catalog, and poll, list, or cancel research jobs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode

from thytrader.agent_http import AgentHttpError, request_json, request_mutation_json
from thytrader.research.http_common import (
    _SYNC_SUBMIT_TIMEOUT_SECONDS,
    _as_object,
    _encode,
    _sync_wait_elapsed,
)
from thytrader.research.mutation import ResearchMutationError

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.research.study_start import ResearchStudyStartRequest


# The API plans the study and auto-binds its datasets before it queues the job, which can
# take well over 5 s on a large sweep; the study still persists, so a short client timeout
# only turned a success into an alarming "ambiguous submit" error.
ASYNC_SUBMIT_TIMEOUT_SECONDS = 30.0


def show_backtest_job(base_url: str, job_id: str) -> str:
    """GET one async backtest job status."""
    return show_research_job(base_url, job_id)


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
