"""Context-independent canonical JSON encoding of typed domain evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, cast
from uuid import UUID

from thytrader.decimal_text import canonical_decimal
from thytrader.exchanges.fees import FEE_PROFILE_FIELDS_OMITTED_WHEN_UNSET
from thytrader.replay.numeric import validate_decimal
from thytrader.trading.models import ORDER_FIELDS_OMITTED_WHEN_UNSET

if TYPE_CHECKING:
    from pydantic import JsonValue


_OMITTED_WHEN_UNSET = ORDER_FIELDS_OMITTED_WHEN_UNSET | FEE_PROFILE_FIELDS_OMITTED_WHEN_UNSET


def wire_value(value: object) -> JsonValue:
    """Normalize only typed Decimal/UTC/UUID values; preserve ordinary strings exactly.

    The dynamic boundary is a validated model's Python-mode dump, never a business-state
    dictionary. Decimal normalization does not use the ambient arithmetic context.
    """
    if isinstance(value, Decimal):
        return canonical_decimal(validate_decimal(value))
    if isinstance(value, datetime):
        if value.utcoffset() != timedelta(0):
            raise ValueError("replay timestamps must be UTC")
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return wire_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, dict):
        # Model-dump keys are declared string field names; recursively validate all values.
        fields = cast("dict[str, object]", value)
        return {key: wire_value(item) for key, item in fields.items()}
    if isinstance(value, (tuple, list)):
        return [wire_value(item) for item in value]
    raise ValueError("unsupported or binary-floating replay value")


def validate_no_coercion(source: JsonValue, checked: JsonValue) -> None:
    """Refuse numeric/boolean coercion hidden inside existing nested domain adapters."""
    if isinstance(source, dict) and isinstance(checked, dict):
        for key, value in source.items():
            if key not in checked:
                # A declared optional field left unset is omitted on output (order
                # timestamps, ADR 0141; fee tier ladder, ADR 0144), so an explicit null for
                # it is not an unknown field.
                if value is None and key in _OMITTED_WHEN_UNSET:
                    continue
                raise ValueError("unknown replay field")
            validate_no_coercion(value, checked[key])
    elif isinstance(source, list) and isinstance(checked, list):
        if len(source) != len(checked):
            raise ValueError("replay collection changed during validation")
        for value, result in zip(source, checked, strict=True):
            validate_no_coercion(value, result)
    elif type(source) is not type(checked):
        raise TypeError("replay fields must retain their JSON type without coercion")
