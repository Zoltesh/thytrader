"""Shared helpers of the research HTTP client.

JSON-object and string checks at the API boundary, stable JSON encoding for agent
output, and the handoff when a synchronous submit outlives the server's wait.
"""

from __future__ import annotations

import json

from thytrader.research.mutation import ResearchMutationError

# A synchronous submit waits server-side for at most ``research_sync_wait_seconds``
# (default 25 s) and then answers 202 with the job, so the client allows a margin.
_SYNC_SUBMIT_TIMEOUT_SECONDS = 60.0


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
