"""Shared HTTP mapping for mutable-strategy failures and start-time snapshots.

Every start endpoint (backtest, study, deployment) resolves ``strategy_id`` to a
snapshot of the current definition through :func:`snapshot_for_start`, so the
same stable error codes reach agents and the browser from every lane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from thytrader.strategies.library import (
    StrategyDeletionBlockedError,
    StrategyDocumentError,
    StrategyInvalidError,
    StrategyLibraryError,
    StrategyNotFoundError,
    StrategyRevisionConflictError,
    StrategySnapshotNotFoundError,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.strategies.library import StrategyStore
    from thytrader.strategies.snapshots import StrategySnapshot


def strategy_http_error(error: StrategyLibraryError) -> HTTPException:
    """Map one redacted strategy failure onto its stable HTTP status and code."""
    if isinstance(error, StrategyNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, "strategy_not_found", "Strategy was not found.")
    if isinstance(error, StrategySnapshotNotFoundError):
        return _error(
            status.HTTP_404_NOT_FOUND,
            "strategy_snapshot_not_found",
            "Strategy snapshot was not found.",
        )
    if isinstance(error, StrategyRevisionConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "strategy_revision_conflict",
                "message": str(error),
                "current_revision": error.current_revision,
            },
        )
    if isinstance(error, StrategyDeletionBlockedError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "strategy_has_active_deployments",
                "message": str(error),
                "deployment_ids": [str(item) for item in error.deployment_ids],
            },
        )
    if isinstance(error, StrategyInvalidError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "strategy_invalid",
                "message": str(error),
                "issues": [{"loc": item.loc, "message": item.message} for item in error.issues],
            },
        )
    if isinstance(error, StrategyDocumentError):
        return _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "strategy_document_invalid", str(error)
        )
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "strategy_storage_unavailable",
        "Strategy storage is unavailable.",
    )


async def snapshot_for_start(store: StrategyStore, strategy_id: UUID) -> StrategySnapshot:
    """Snapshot the strategy's current valid definition or raise the mapped HTTP error."""
    try:
        return await store.snapshot(strategy_id)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    """Build one ``{"detail": {"code", "message"}}`` error."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
