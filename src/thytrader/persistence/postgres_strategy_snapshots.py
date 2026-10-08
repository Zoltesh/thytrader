"""Content-addressed strategy snapshot rows and dataset binding checks.

Snapshots are verified on every load: stored bytes must be canonical, hash to their
fingerprint, and name the owning strategy. :func:`snapshot_owner` lets research and runtime
rows copy their ``strategy_id`` from the snapshot they reference, and a dataset binding
accepts only a dataset the snapshot's document reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from sqlalchemy import ScalarSelect, select
from sqlalchemy.dialects.postgresql import insert

from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.schema import strategy_snapshots
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    covered_product_ids,
    expanded_data_requirements,
    reference_series,
    strategy_fingerprint,
)
from thytrader.strategies.snapshots import (
    StrategyDatasetMismatchError,
    StrategySnapshot,
    StrategySnapshotError,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.market_data.datasets import DatasetStore


_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


async def _store_snapshot(
    connection: AsyncConnection, definition: StrategyDefinition
) -> StrategySnapshot:
    """Insert (or reuse) one snapshot row and verify the stored bytes."""
    canonical = canonical_strategy_bytes(definition).decode("utf-8")
    fingerprint = strategy_fingerprint(definition)
    await connection.execute(
        insert(strategy_snapshots)
        .values(
            strategy_fingerprint=fingerprint,
            strategy_id=str(definition.strategy_id),
            canonical_definition=canonical,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["strategy_fingerprint"])
    )
    result = await connection.execute(
        select(
            strategy_snapshots.c.strategy_id,
            strategy_snapshots.c.canonical_definition,
        ).where(strategy_snapshots.c.strategy_fingerprint == fingerprint)
    )
    return _snapshot_from_row(result.mappings().one(), fingerprint)


def _snapshot_from_row(row: RowMapping, strategy_fingerprint_value: str) -> StrategySnapshot:
    """Validate one snapshot row against its canonical identity and owner."""
    canonical = cast("str", row["canonical_definition"])
    try:
        definition = StrategyDefinition.model_validate_json(canonical)
    except ValidationError as error:
        raise StrategySnapshotError("Strategy snapshot content failed validation.") from error
    if canonical_strategy_bytes(definition).decode("utf-8") != canonical:
        raise StrategySnapshotError("Strategy snapshot bytes are not canonical.")
    if strategy_fingerprint(definition) != strategy_fingerprint_value:
        raise StrategySnapshotError("Strategy snapshot fingerprint verification failed.")
    owner = cast("str | None", row["strategy_id"])
    if owner is not None and owner != str(definition.strategy_id):
        raise StrategySnapshotError("Strategy snapshot owner does not match its document.")
    return StrategySnapshot(strategy_fingerprint=strategy_fingerprint_value, definition=definition)


def _verify_compatible_dataset(
    snapshot: StrategySnapshot,
    dataset_fingerprint: str,
    dataset_store: DatasetStore,
) -> None:
    """Verify immutable dataset availability and strategy identity compatibility.

    A multi-instrument document (ADR 0056) runs the same timeframes on every covered
    product, so a dataset matches when its product is any covered product and its
    timeframe is one the document reads. A declared reference instrument (ADR 0096)
    also matches its exact product and timeframe.
    """
    try:
        manifest = dataset_store.load_manifest(dataset_fingerprint)
    except (DatasetStoreError, OSError, ValueError) as error:
        raise StrategySnapshotError(
            "Immutable dataset could not be verified for strategy binding."
        ) from error
    definition = snapshot.definition
    allowed_products = covered_product_ids(definition)
    allowed_timeframes = {
        requirement.timeframe for requirement in expanded_data_requirements(definition)
    }
    covered = manifest.product_id in allowed_products and manifest.timeframe in allowed_timeframes
    referenced = (manifest.product_id, manifest.timeframe) in reference_series(definition)
    if manifest.provider != "coinbase" or not (covered or referenced):
        references = "".join(
            f"; reference {product_id} {timeframe}"
            for product_id, timeframe in sorted(reference_series(definition))
        )
        raise StrategyDatasetMismatchError(
            f"Dataset {manifest.product_id} {manifest.timeframe} ({manifest.provider}) does not "
            f"match the strategy: it covers {', '.join(allowed_products)} on "
            f"{', '.join(sorted(allowed_timeframes))}{references} (coinbase)."
        )


def _validate_fingerprint(value: str, *, label: str) -> None:
    """Reject malformed content identities before filesystem or SQL lookup."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise StrategySnapshotError(f"Invalid {label} fingerprint.")


def snapshot_owner(strategy_fingerprint_value: str) -> ScalarSelect[str | None]:
    """Scalar subquery resolving a snapshot fingerprint to its owning strategy_id.

    Research and runtime rows copy their ``strategy_id`` from the snapshot they
    reference, so a row can never claim a strategy its rules did not come from.
    A detached snapshot (owner deleted) yields NULL and the NOT NULL insert fails.
    """
    return (
        select(strategy_snapshots.c.strategy_id)
        .where(strategy_snapshots.c.strategy_fingerprint == strategy_fingerprint_value)
        .scalar_subquery()
    )
