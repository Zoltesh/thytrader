"""Restore UTC-singleton domain timestamps after portable JSON decoding, without global patches."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import BaseModel


def restore_utc(value: object) -> object:
    """Normalize only already-typed timestamps; reject naive/non-UTC rather than repairing them.

    Existing domain dataclasses and consumers use ``tzinfo is UTC``. Pydantic JSON returns
    its own zero-offset tzinfo, so this adapter restores that exact domain representation.
    The dynamic walk is confined to validated evidence, never arbitrary business dictionaries.
    """
    if isinstance(value, datetime):
        if value.utcoffset() != timedelta(0):
            raise ValueError("replay domain timestamps must be UTC")
        return value.astimezone(UTC)
    if isinstance(value, BaseModel):
        return value.model_copy(
            update={name: restore_utc(getattr(value, name)) for name in type(value).model_fields}
        )
    if is_dataclass(value) and not isinstance(value, type):
        return replace(
            value,
            **{field.name: restore_utc(getattr(value, field.name)) for field in fields(value)},
        )
    if isinstance(value, tuple):
        return tuple(restore_utc(item) for item in value)
    if isinstance(value, list):
        return [restore_utc(item) for item in value]
    if isinstance(value, dict):
        values = cast("dict[str, object]", value)
        return {key: restore_utc(item) for key, item in values.items()}
    return value
