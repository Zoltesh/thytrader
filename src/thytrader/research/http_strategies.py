"""Research HTTP client for the strategy library.

Create, show, save, import, clone, and (bulk) delete strategies; page the library;
read snapshots and templates.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlencode
from uuid import UUID

from pydantic import ValidationError

from thytrader.agent_http import request_json, request_mutation_json
from thytrader.memory.models import ExperientialModel
from thytrader.ops_contract import STALE_IMAGE_REBUILD
from thytrader.research.http_common import _as_object, _as_str, _encode
from thytrader.research.mutation import ResearchMutationError
from thytrader.strategies.library import StrategyOrigin

if TYPE_CHECKING:
    from thytrader.strategies.library import StrategyDocument


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


def _strategy_source(base_url: str, strategy_fingerprint: str) -> dict[str, object]:
    """Load one strategy snapshot document."""
    return _as_object(
        request_json(
            method="GET",
            url=f"{base_url}/api/v1/strategies/snapshots/{strategy_fingerprint}",
        ),
        "strategy snapshot",
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
