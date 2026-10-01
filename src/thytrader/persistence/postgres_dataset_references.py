"""PostgreSQL scan for every persisted dataset fingerprint reference.

Dataset fingerprints appear in typed columns (``strategy_dataset_bindings``, run specs,
backtest results, worker state) and inside JSON payloads (study plans, fold specs,
deployment and job requests). Rather than maintain a list that a future table could
silently escape, retention scans every text and JSON column in the current schema and
extracts ``sha256:<64 hex>`` tokens in SQL. Any fingerprint-shaped token counts as a
reference, so strategy or result fingerprints only make the keep-set larger, never
smaller.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_FINGERPRINT_PATTERN = "sha256:[0-9a-f]{64}"
_FINGERPRINT = re.compile(rf"^{_FINGERPRINT_PATTERN}$")
_SCANNED_TYPES = ("text", "character varying", "json", "jsonb", "character")
_COLUMNS_QUERY = text(
    """
    SELECT table_name, column_name
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND data_type = ANY(:types)
    ORDER BY table_name, column_name
    """
)


def _quote_identifier(name: str) -> str:
    """Quote one catalog identifier for interpolation into a scan statement."""
    return '"' + name.replace('"', '""') + '"'


class PostgresDatasetReferenceSource:
    """Collect fingerprint tokens from every text and JSON column of the schema."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind scans to one managed async engine."""
        self._engine = engine

    async def referenced_fingerprints(self) -> frozenset[str]:
        """Return every fingerprint token in one repeatable-read snapshot."""
        found: set[str] = set()
        async with self._engine.connect() as connection:
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            columns = (
                await connection.execute(_COLUMNS_QUERY, {"types": list(_SCANNED_TYPES)})
            ).all()
            for table_name, column_name in columns:
                table = _quote_identifier(str(table_name))
                column = _quote_identifier(str(column_name))
                statement = text(
                    f"SELECT DISTINCT (regexp_matches({column}::text, :pattern, 'g'))[1] "  # noqa: S608 - identifiers come from the catalog and are quoted.
                    f"FROM {table} WHERE {column}::text LIKE '%sha256:%'"
                )
                rows = (
                    await connection.execute(statement, {"pattern": _FINGERPRINT_PATTERN})
                ).all()
                found.update(str(row[0]) for row in rows if _FINGERPRINT.fullmatch(str(row[0])))
        return frozenset(found)
