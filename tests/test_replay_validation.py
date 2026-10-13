"""Every selected auxiliary input is validated again at the canonical package boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest

from thytrader.replay.models import ReplayPackage, canonical_bytes
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint


def auxiliary_payload() -> dict[str, Any]:
    """Author synthetic JSON at a dynamic fixture boundary with three auxiliary clocks."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    definition = payload["strategy"]
    definition["data_requirements"]["reference_instruments"] = [
        {"id": "btc_reference", "product_id": "ETH-USD", "timeframe": "2h"}
    ]
    definition["indicators"].extend(
        [
            {
                "id": "ref_close",
                "kind": "identity",
                "input": "close",
                "parameters": {},
                "source": "btc_reference",
            },
            {
                "id": "extra_close",
                "kind": "identity",
                "input": "close",
                "parameters": {},
                "timeframe": "2h",
            },
        ]
    )
    definition["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {"warmup_bars": 1, "required_fields": ["close"]},
        "indicators": [{"id": "htf_close", "kind": "identity", "input": "close", "parameters": {}}],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_close"},
                    "operator": "greater_than",
                    "right": {"literal": "0"},
                }
            ]
        },
    }
    checked = StrategyDefinition.model_validate_json(json.dumps(definition))
    step = payload["steps"][0]
    step["before"]["deployment"]["strategy_fingerprint"] = strategy_fingerprint(checked)
    anchor = datetime(2026, 1, 1, tzinfo=UTC)

    def rows(hours: int) -> list[dict[str, str]]:
        """Keep all bars closed before this synthetic decision."""
        return [
            {
                "starts_at": (anchor + timedelta(hours=hours * i)).isoformat(),
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10",
                "volume": "1",
            }
            for i in range(3)
        ]

    step["htf_candles"] = rows(4)
    step["indicator_windows"] = [
        {"key": "2h", "product_id": "BTC-USD", "timeframe": "2h", "candles": rows(2)}
    ]
    step["reference_windows"] = [
        {"key": "btc_reference", "product_id": "ETH-USD", "timeframe": "2h", "candles": rows(2)}
    ]
    return payload


@pytest.mark.parametrize("field", ["htf_candles", "indicator_windows", "reference_windows"])
@pytest.mark.parametrize("fault", ["gap", "duplicate", "reorder", "future", "naive", "value"])
def test_auxiliary_candles_fail_closed(field: str, fault: str) -> None:
    """Malformed selected HTF, extra-clock and reference bars never receive canonical identity."""
    payload = auxiliary_payload()
    rows = payload["steps"][0][field]
    if field != "htf_candles":
        rows = rows[0]["candles"]
    if fault == "gap":
        rows.pop(1)
    elif fault == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif fault == "reorder":
        rows.reverse()
    elif fault == "future":
        rows[-1]["starts_at"] = "2099-01-01T00:00:00Z"
    elif fault == "naive":
        rows[0]["starts_at"] = "2026-01-01T00:00:00"
    else:
        rows[0]["high"] = "1"
    with pytest.raises(ValueError):
        ReplayPackage.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["indicator_windows", "reference_windows"])
@pytest.mark.parametrize("fault", ["key", "product_id", "timeframe", "duplicate"])
def test_auxiliary_source_identity_fail_closed(field: str, fault: str) -> None:
    """Named series identity and unique keys must agree with the immutable strategy."""
    payload = auxiliary_payload()
    windows = payload["steps"][0][field]
    if fault == "duplicate":
        windows.append(deepcopy(windows[0]))
    else:
        windows[0][fault] = "4h" if fault == "timeframe" else "wrong"
    with pytest.raises(ValueError):
        ReplayPackage.model_validate_json(json.dumps(payload))


def test_forged_model_copy_auxiliary_rejected_at_canonical_boundary() -> None:
    """Validated-model copy APIs cannot bypass selected auxiliary validation."""
    package = ReplayPackage.model_validate_json(json.dumps(auxiliary_payload()))
    step = package.steps[0]
    forged = step.model_copy(update={"reference_windows": step.reference_windows * 2})
    with pytest.raises(ValueError):
        canonical_bytes(package.model_copy(update={"steps": (forged,)}))


def test_auxiliary_valid_control() -> None:
    """Normal independently declared clocks remain canonicalizable."""
    package = ReplayPackage.model_validate_json(json.dumps(auxiliary_payload()))
    assert canonical_bytes(package)


def test_decoded_domain_inputs_restore_existing_utc_contract() -> None:
    """Domain consumers requiring the UTC singleton must accept normalized portable JSON."""
    package = ReplayPackage.model_validate_json(json.dumps(auxiliary_payload()))
    assert package.steps[0].candles[0].starts_at.tzinfo is UTC
    assert package.steps[0].before.deployment.created_at.tzinfo is UTC


def test_canonical_model_copy_cannot_coerce_financial_json_number() -> None:
    """Untyped copies cannot sneak a number through domain Decimal coercion when fingerprinting."""
    package = ReplayPackage.model_validate_json(json.dumps(auxiliary_payload()))
    step = package.steps[0]
    # Deliberately break a domain annotation at an untrusted model-copy boundary.
    forged = replace(step.before.deployment)
    object.__setattr__(forged, "cash", 10000)
    before = replace(step.before, deployment=forged)
    with pytest.raises((TypeError, ValueError)):
        canonical_bytes(
            package.model_copy(update={"steps": (step.model_copy(update={"before": before}),)})
        )


@pytest.mark.parametrize("fault", ["product", "timeframe", "misaligned"])
def test_primary_source_identity_and_clock_are_not_inferred(fault: str) -> None:
    """Primary identity must match the immutable strategy, book and venue metadata."""
    payload = auxiliary_payload()
    step = payload["steps"][0]
    if fault == "product":
        step["product"]["product_id"] = "WRONG-USD"
    elif fault == "timeframe":
        step["before"]["deployment"]["timeframe"] = "2h"
    else:
        for row in step["candles"]:
            row["starts_at"] = (
                datetime.fromisoformat(row["starts_at"]) + timedelta(seconds=1)
            ).isoformat()
    with pytest.raises(ValueError):
        ReplayPackage.model_validate_json(json.dumps(payload))
