"""HTTP glue for research dataset binding shared by backtest and study routes (ADR 0089)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from thytrader.data_control.service import ingestion_provider
from thytrader.research.dataset_binding import DatasetResolver, DatasetsMissingError

if TYPE_CHECKING:
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.runtime import RuntimeState


def dataset_resolver(datasets: DatasetStore, runtime: RuntimeState) -> DatasetResolver:
    """Resolve omitted datasets from the provider the market-data worker ingests."""
    return DatasetResolver(store=datasets, provider=ingestion_provider(runtime.settings))


def datasets_missing_http_error(error: DatasetsMissingError) -> HTTPException:
    """Map missing catalog datasets to a 422 that lists each clock and the fix."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "datasets_missing",
            "message": str(error),
            "provider": error.provider,
            "missing": [
                {"product_id": need.product_id, "timeframe": need.timeframe, "role": need.role}
                for need in error.missing
            ],
        },
    )
