"""Validate external expected JSON against recomputed CLI outcomes; never use it as engine input."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thytrader.replay.codec import wire_value
from thytrader.replay.configuration import differences
from thytrader.replay.packages import MAX_PACKAGE_BYTES, validate_no_coercion
from thytrader.replay.runner import AcceptanceComparison, ReplayReport

if TYPE_CHECKING:
    from pathlib import Path


def check_expected(report: ReplayReport, path: Path) -> ReplayReport:
    """Check the public schema, then compare all report fields except the check's own result."""
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("expected report exceeds bounded size")
    content = path.read_bytes()
    raw = json.loads(content)
    expected = ReplayReport.model_validate_json(content)
    expected_wire = wire_value(expected.model_dump(mode="python"))
    actual_wire = wire_value(report.model_dump(mode="python"))
    if not isinstance(expected_wire, dict) or not isinstance(actual_wire, dict):
        raise TypeError("report must be a JSON object")
    validate_no_coercion(raw, expected_wire)
    expected_wire.pop("acceptance", None)
    actual_wire.pop("acceptance", None)
    changed = tuple(differences(expected_wire, actual_wire))
    return report.model_copy(
        update={
            "acceptance": AcceptanceComparison(
                status="mismatch" if changed else "match", differences=changed
            )
        }
    )
