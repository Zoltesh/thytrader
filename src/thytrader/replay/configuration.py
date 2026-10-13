"""Financial-rule comparison keeps tested rules separate from deployed replay evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue, TypeAdapter

from thytrader.strategies.models import canonical_strategy_bytes

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition


def configuration_differences(
    original: StrategyDefinition, deployed: StrategyDefinition
) -> tuple[str, ...]:
    """Ignore only identity/annotations, retaining every financial and execution-rule field."""
    aligned = original.model_copy(
        update={
            "strategy_id": deployed.strategy_id,
            "name": deployed.name,
            "description": deployed.description,
            "created_at": deployed.created_at,
            "metadata": deployed.metadata,
        }
    )
    adapter = TypeAdapter(JsonValue)
    return differences(
        adapter.validate_json(canonical_strategy_bytes(aligned)),
        adapter.validate_json(canonical_strategy_bytes(deployed)),
    )


def differences(original: JsonValue, deployed: JsonValue, path: str = "") -> tuple[str, ...]:
    """Return exact differing JSON field paths with deterministic ordering."""
    if isinstance(original, dict) and isinstance(deployed, dict):
        return tuple(
            item
            for key in sorted(original.keys() | deployed.keys())
            for item in differences(
                original.get(key), deployed.get(key), f"{path}.{key}" if path else key
            )
        )
    if original != deployed:
        return (path,)
    return ()
