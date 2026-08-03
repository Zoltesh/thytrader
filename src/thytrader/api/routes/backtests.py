"""Read-only immutable backtest result HTTP presentation.

These endpoints expose historical simulation evidence only. They cannot submit
a backtest, mutate an immutable result, or grant any trading authority.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from thytrader.api.dependencies import get_backtest_result_store
from thytrader.backtest.models import BacktestResult, BacktestSummary  # noqa: TC001
from thytrader.persistence.backtest_results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultReader,
    BacktestResultSummaryView,
    BacktestResultUnavailableError,
)

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])
_logger = logging.getLogger(__name__)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_LIMIT = 100


class BacktestSummaryResponse(BaseModel):
    """One newest-first immutable result summary safe for browser discovery."""

    model_config = ConfigDict(from_attributes=True)
    result_fingerprint: str
    run_fingerprint: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    engine_contract_version: str
    published_at: str
    summary: BacktestSummary


class BacktestListResponse(BaseModel):
    """Bounded page of immutable backtest result summaries."""

    entries: tuple[BacktestSummaryResponse, ...]
    limit: int
    offset: int
    returned: int


class BacktestDetailResponse(BaseModel):
    """One fully reverified immutable simulation result."""

    model_config = ConfigDict(from_attributes=True)
    result: BacktestResult
    result_fingerprint: str


class BacktestErrorDetail(BaseModel):
    """Stable redacted response for backtest-result read failures."""

    code: Literal["backtests_unavailable", "backtest_not_found", "backtest_invalid"]
    message: str


class BacktestErrorResponse(BaseModel):
    """FastAPI-compatible error envelope for backtest-result failures."""

    detail: BacktestErrorDetail


def _fingerprint_or_none(value: str | None) -> str | None:
    """Validate one optional fingerprint filter, rejecting malformed identities."""
    if value is None:
        return None
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Fingerprint filter is malformed."},
        )
    return value


@router.get(
    "",
    response_model=BacktestListResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse}},
)
async def list_backtests(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    run_fingerprint: Annotated[str | None, Query()] = None,
    strategy_fingerprint: Annotated[str | None, Query()] = None,
    dataset_fingerprint: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BacktestListResponse:
    """Return a bounded newest-first page of immutable result summaries."""
    selected = [
        _fingerprint_or_none(value)
        for value in (run_fingerprint, strategy_fingerprint, dataset_fingerprint)
        if value is not None
    ]
    if len(selected) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "backtest_invalid",
                "message": "Only one source fingerprint filter is accepted per request.",
            },
        )
    try:
        entries = await store.list_summaries(
            run_fingerprint=_fingerprint_or_none(run_fingerprint),
            strategy_fingerprint=_fingerprint_or_none(strategy_fingerprint),
            dataset_fingerprint=_fingerprint_or_none(dataset_fingerprint),
            limit=limit,
            offset=offset,
        )
    except (BacktestResultUnavailableError, BacktestResultIntegrityError) as error:
        _logger.warning("Backtest list failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Backtest list failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    return BacktestListResponse(
        entries=tuple(_to_summary_response(entry) for entry in entries),
        limit=limit,
        offset=offset,
        returned=len(entries),
    )


@router.get(
    "/{result_fingerprint}",
    response_model=BacktestDetailResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": BacktestErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": BacktestErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse},
    },
)
async def get_backtest(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    result_fingerprint: str,
) -> BacktestDetailResponse:
    """Load and fully reverify one immutable result before returning it."""
    if _FINGERPRINT_PATTERN.fullmatch(result_fingerprint) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Result fingerprint is malformed."},
        )
    try:
        result = await store.load(result_fingerprint)
    except BacktestResultNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_not_found", "message": "Backtest result was not found."},
        ) from None
    except (BacktestResultUnavailableError, BacktestResultIntegrityError) as error:
        _logger.warning("Backtest detail failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Backtest detail failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    return BacktestDetailResponse(result=result, result_fingerprint=result_fingerprint)


def _to_summary_response(entry: BacktestResultSummaryView) -> BacktestSummaryResponse:
    """Map one discovery view into its browser-safe response."""
    return BacktestSummaryResponse(
        result_fingerprint=entry.result_fingerprint,
        run_fingerprint=entry.run_fingerprint,
        strategy_fingerprint=entry.strategy_fingerprint,
        dataset_fingerprint=entry.dataset_fingerprint,
        engine_contract_version=entry.engine_contract_version,
        published_at=entry.published_at.isoformat().replace("+00:00", "Z"),
        summary=entry.summary,
    )
