"""Live PostgreSQL coverage for the portfolio snapshot history store."""

from __future__ import annotations

import asyncio
import json
import os

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.persistence.test_snapshot_serialization import _sample_portfolio
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_history import (
    PostgresPortfolioHistoryStore,
    _portfolio_to_snapshot,
)

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_recorded_snapshot_is_a_json_object_with_exact_decimal_strings() -> None:
    """The snapshot column stores the portfolio as a JSON object, never a JSON string."""

    async def exercise() -> None:
        assert _TEST_DATABASE_URL is not None
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        portfolio = _sample_portfolio()
        try:
            await PostgresPortfolioHistoryStore(engine).record(portfolio)
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT json_typeof(snapshot) AS kind, snapshot::text AS body "
                            "FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
                        )
                    )
                ).one()
        finally:
            await dispose(engine)
        assert row.kind == "object"
        assert json.loads(row.body) == _portfolio_to_snapshot(portfolio)

    asyncio.run(exercise())
