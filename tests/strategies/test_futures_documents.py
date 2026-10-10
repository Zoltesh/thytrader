"""Futures strategy documents and run-spec fields (ADR 0128, slice P1-1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
import pytest

from thytrader.backtest.kernel import BacktestSimulationError, simulate_backtest
from thytrader.evaluation.models import ResearchRunSpecification, research_run_fingerprint
from thytrader.risk.gate import evaluate_new_deployment
from thytrader.risk.models import RiskDecision, RiskReasonCode, compiled_default_risk_policy
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.trading.models import DeploymentMode

_SPOT = Path("tests/strategies/golden/sma_strategy_v1.json")
_SPEC = Path("tests/evaluation/golden/reference_run_spec.json")
_FUTURE_INSTRUMENT = {
    "product_id": "BIP-20DEC30-CDE",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "kind": "future",
}


def _spot_document() -> dict[str, Any]:
    """The pinned spot golden document."""
    payload: dict[str, Any] = json.loads(_SPOT.read_text())
    return payload


def _future_document(**overrides: Any) -> dict[str, Any]:
    """The spot golden re-targeted at the BTC perp with a derivatives block."""
    document = _spot_document()
    document["instrument"] = dict(_FUTURE_INSTRUMENT)
    document["derivatives"] = {"max_leverage": "2"}
    document.update(overrides)
    return document


def test_spot_documents_keep_their_bytes() -> None:
    """``kind`` and ``derivatives`` never appear in a spot document's canonical bytes."""
    spot = StrategyDefinition.model_validate(_spot_document())
    canonical = json.loads(canonical_strategy_bytes(spot))
    assert "kind" not in canonical["instrument"]
    assert "derivatives" not in canonical
    assert spot.instrument.is_future is False


def test_futures_document_validates_and_has_its_own_identity() -> None:
    """A perp document carries kind and derivatives and fingerprints differently."""
    future = StrategyDefinition.model_validate(_future_document())
    spot = StrategyDefinition.model_validate(_spot_document())
    canonical = json.loads(canonical_strategy_bytes(future))
    assert canonical["instrument"]["kind"] == "future"
    assert canonical["derivatives"] == {"margin_mode": "overnight", "max_leverage": "2"}
    assert future.instrument.is_future
    assert strategy_fingerprint(future) != strategy_fingerprint(spot)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"derivatives": None}, "derivatives block"),
        (
            {"instrument": {**_FUTURE_INSTRUMENT, "quote_currency": "USDC"}},
            "settles in USD",
        ),
        (
            {"instrument": {**_FUTURE_INSTRUMENT, "product_id": "BTC-USD"}},
            "CODE-DDMONYY-CDE",
        ),
        ({"derivatives": {"max_leverage": "0.5"}}, "between 1 and 20"),
        ({"derivatives": {"max_leverage": "21"}}, "between 1 and 20"),
        ({"derivatives": {"max_leverage": "2", "margin_mode": "intraday"}}, "overnight"),
    ],
)
def test_invalid_futures_documents(change: dict[str, Any], message: str) -> None:
    """Each futures rule fails with a named reason."""
    document = _future_document()
    for key, value in change.items():
        if value is None:
            document.pop(key)
        else:
            document[key] = value
    with pytest.raises(ValidationError, match=message):
        StrategyDefinition.model_validate(document)


def test_derivatives_need_a_futures_instrument_and_futures_stay_single() -> None:
    """Spot documents cannot carry derivatives; futures cover one product and no references."""
    spot = _spot_document()
    spot["derivatives"] = {"max_leverage": "2"}
    with pytest.raises(ValidationError, match=r"only allowed with instrument\.kind future"):
        StrategyDefinition.model_validate(spot)
    covered = _future_document(
        additional_instruments=[
            {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
        ]
    )
    with pytest.raises(ValidationError, match="additional instruments"):
        StrategyDefinition.model_validate(covered)
    future_extra = _spot_document()
    future_extra["additional_instruments"] = [_FUTURE_INSTRUMENT]
    with pytest.raises(ValidationError, match="futures are single-instrument"):
        StrategyDefinition.model_validate(future_extra)


def _spec(**overrides: Any) -> dict[str, Any]:
    """The pinned spot golden run spec with overrides."""
    payload: dict[str, Any] = json.loads(_SPEC.read_text())
    payload.update(overrides)
    return payload


def _contract(kind: str = "perpetual_future") -> dict[str, Any]:
    """One bound contract."""
    return {
        "product_id": "BIP-20DEC30-CDE",
        "kind": kind,
        "underlying": "BTC",
        "contract_size": "0.01",
        "expires_at": None if kind == "perpetual_future" else "2030-12-20T16:00:00Z",
        "listed_expiry": "2030-12-20",
        "catalog_fingerprint": "sha256:" + "c" * 64,
    }


_MARGIN = {
    "long_rate": "0.21025",
    "short_rate": "0.256625",
    "source": "latest_observation",
    "observed_at": "2026-10-10T00:00:00Z",
}
_FUNDING = {"series_fingerprint": "sha256:" + "f" * 64, "settled_hours": 24}


def _validate_spec(payload: dict[str, Any]) -> ResearchRunSpecification:
    """Validate a JSON-shaped spec the way stored specs are read."""
    return ResearchRunSpecification.model_validate_json(json.dumps(payload))


def test_spot_run_spec_bytes_and_fingerprint_are_unchanged() -> None:
    """The golden spot spec round-trips without any futures key."""
    spec = _validate_spec(_spec())
    dumped = spec.model_dump(mode="json")
    for key in ("instrument_contract", "margin", "funding"):
        assert key not in dumped
    assert "fee_per_contract" not in dumped["costs"]
    assert research_run_fingerprint(spec).startswith("sha256:")


def test_futures_run_spec_binds_contract_margin_and_funding() -> None:
    """A perp spec carries the three bindings and a per-contract fee."""
    payload = _spec(instrument_contract=_contract(), margin=_MARGIN, funding=_FUNDING)
    payload["capital"] = {**payload["capital"], "quote_currency": "USD"}
    payload["costs"] = {**payload["costs"], "fee_per_contract": "0.01"}
    spec = _validate_spec(payload)
    assert spec.instrument_contract is not None
    assert spec.instrument_contract.listed_expiry == date(2030, 12, 20)
    assert spec.margin is not None
    assert spec.margin.observed_at == datetime(2026, 10, 10, tzinfo=UTC)
    dumped = spec.model_dump(mode="json")
    assert dumped["margin"]["stress_multiplier"] == "1"
    assert research_run_fingerprint(spec) != research_run_fingerprint(_validate_spec(_spec()))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"instrument_contract": _contract()}, "set together"),
        ({"instrument_contract": _contract(), "margin": _MARGIN}, "funding is required"),
        (
            {
                "instrument_contract": _contract("dated_future"),
                "margin": _MARGIN,
                "funding": _FUNDING,
            },
            "funding is required",
        ),
        (
            {
                "instrument_contract": _contract(),
                "margin": _MARGIN,
                "funding": {**_FUNDING, "constant_rate": "0.00001"},
            },
            "exactly one",
        ),
        (
            {
                "instrument_contract": _contract(),
                "margin": {**_MARGIN, "observed_at": None},
                "funding": _FUNDING,
            },
            "observed_at",
        ),
        (
            {"instrument_contract": {**_contract(), "expires_at": "2030-12-20T16:00:00Z"}},
            "expires_at",
        ),
    ],
)
def test_invalid_futures_run_specs(overrides: dict[str, Any], message: str) -> None:
    """Incomplete or inconsistent futures bindings are refused."""
    payload = _spec(**overrides)
    payload["capital"] = {**payload["capital"], "quote_currency": "USD"}
    with pytest.raises(ValidationError, match=message):
        _validate_spec(payload)


def test_spot_specs_refuse_futures_only_fields_and_futures_need_usd() -> None:
    """A per-contract fee needs a contract; a futures run's capital is USD."""
    payload = _spec()
    payload["costs"] = {**payload["costs"], "fee_per_contract": "0.01"}
    with pytest.raises(ValidationError, match="futures runs only"):
        _validate_spec(payload)
    usdc = _spec(instrument_contract=_contract(), margin=_MARGIN, funding=_FUNDING)
    usdc["capital"] = {**usdc["capital"], "quote_currency": "USDC"}
    with pytest.raises(ValidationError, match="capital is in USD"):
        _validate_spec(usdc)


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        (DeploymentMode.LIVE, RiskReasonCode.FUTURES_LIVE_UNSUPPORTED),
        (DeploymentMode.PAPER, RiskReasonCode.FUTURES_PAPER_UNSUPPORTED),
    ],
)
def test_futures_strategies_cannot_be_deployed(mode: DeploymentMode, code: RiskReasonCode) -> None:
    """No deployment path admits a futures product in this release."""
    verdict = evaluate_new_deployment(
        compiled_default_risk_policy(),
        mode=mode,
        product_id="BIP-20DEC30-CDE",
        strategy_id=uuid4(),
        paper_starting_cash=None,
        deployments=(),
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is code


def test_kernel_refuses_a_futures_strategy_with_a_spot_run_spec() -> None:
    """The kernel never simulates a futures strategy without its contract and margin."""
    future = StrategyDefinition.model_validate(_future_document())
    payload = _spec(strategy_fingerprint=strategy_fingerprint(future))
    spec = _validate_spec(payload)
    with pytest.raises(BacktestSimulationError, match="FUTURES_SPEC_MISMATCH"):
        simulate_backtest(spec, future, ())


def test_futures_documents_cannot_pyramid() -> None:
    """One position per futures book in P1."""
    document = _future_document()
    document["entry"] = {
        **document["entry"],
        "max_open_positions": 2,
        "pyramiding": {"enabled": True},
    }
    with pytest.raises(ValidationError, match="cannot pyramid"):
        StrategyDefinition.model_validate(document)
