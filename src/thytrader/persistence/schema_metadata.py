"""Shared SQLAlchemy Core ``MetaData`` for every ThyTrader operational table.

Per-domain table modules under :mod:`thytrader.persistence.tables` register their
tables on this single object. Import :mod:`thytrader.persistence.schema` (not this
module) when you need every table registered, for example for Alembic.
"""

from __future__ import annotations

from sqlalchemy import MetaData

metadata = MetaData()

# Shared CHECK-constraint regex fragment for ``sha256:<64 hex>`` fingerprint columns.
FINGERPRINT_REGEX = "'^sha256:[0-9a-f]{64}$'"

__all__ = ["FINGERPRINT_REGEX", "metadata"]
