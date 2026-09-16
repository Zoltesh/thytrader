"""Compatibility checks between shipped skills and the operator contract."""

from __future__ import annotations

import json
from pathlib import Path

from thytrader.operator.models import (
    OPERATOR_API_PREFIX,
    REPORT_KINDS,
    SCHEMA_VERSION,
    OperatorEnvelope,
)

_ROOT = Path(__file__).parents[2]
_OPERATOR_SKILL = _ROOT / "skills" / "thytrader-operator" / "SKILL.md"
_DIAGNOSTICS = _ROOT / "skills" / "thytrader-operator" / "references" / "diagnostics-api.md"
_SCHEMAS = _ROOT / "skills" / "thytrader-operator" / "references" / "report-schemas.md"
_RESEARCH_SKILL = _ROOT / "skills" / "thytrader-research" / "SKILL.md"
_RUNTIME_SKILL = _ROOT / "skills" / "thytrader-runtime" / "SKILL.md"
_DATA_SKILL = _ROOT / "skills" / "thytrader-data" / "SKILL.md"
_PLAYBOOK_SKILL = _ROOT / "skills" / "thytrader-playbook" / "SKILL.md"
_MEMORY_SKILL = _ROOT / "skills" / "thytrader-memory" / "SKILL.md"


def test_operator_skill_matches_application_schema_and_routes() -> None:
    """The shipped skill must name the live schema version and HTTP prefix."""
    skill = _OPERATOR_SKILL.read_text(encoding="utf-8")
    diagnostics = _DIAGNOSTICS.read_text(encoding="utf-8")
    schemas = _SCHEMAS.read_text(encoding="utf-8")
    combined = skill + diagnostics + schemas
    assert SCHEMA_VERSION in combined
    assert OPERATOR_API_PREFIX in combined
    for suffix in (
        "/health",
        "/configuration",
        "/exchange",
        "/market-data",
        "/strategies",
        "/performance",
        "/risk",
        "/reconciliation",
        "/runtime",
        "/monitor",
        "/trade-reasons",
        "/data-catalog",
        "/products",
        "/indicators",
        "/support-bundle",
        "/studies",
    ):
        assert f"{OPERATOR_API_PREFIX}{suffix}" in combined
    assert "thytrader-operator" in skill
    assert "schema-check" in skill
    assert "do not edit" in skill.lower()
    assert "make run" in skill
    assert "/api/v1/operator-chat" in skill
    assert "chat-status" in skill
    assert "0051-in-app-operator-chat" in skill
    assert "0055-yaml-settings-runtime-reloadable-yolo" in skill
    assert "yaml_source_of_truth" in schemas
    assert "THYTRADER_YOLO_TIERS=paper" in skill
    assert "1h or 5m" not in skill
    assert "Never places" in skill or "cannot place" in skill.lower() or "Never" in skill
    assert "daily_loss_limit_fraction" in schemas
    assert "DAILY_LOSS_LIMIT" in schemas
    assert "STRATEGY_DRAWDOWN_LIMIT" in schemas
    assert "risk_breakers" in schemas
    assert "experiential_model_engines" in schemas
    assert "trade_reason_journals" in schemas


def test_research_skill_requires_confirm_and_forbids_trading() -> None:
    """The research skill must gate mutations and deny paper/live control."""
    skill = _RESEARCH_SKILL.read_text(encoding="utf-8")
    assert "thytrader-research" in skill
    assert "--confirm" in skill
    assert "create-draft" in skill
    assert "--experiential-model-id" in skill
    assert "--product-id" in skill
    assert "--timeframe" in skill
    assert "submit-backtest" in skill
    assert "submit-study" in skill
    assert "plan-study" in skill
    assert "list-studies" in skill
    assert "show-study" in skill
    assert "parameter_sweep" in skill
    assert "walk_forward_optimization" in skill
    assert "stitched" in skill.lower()
    assert "list-templates" in skill
    assert "engine-support" in skill
    assert "--template" in skill
    assert "macd" in skill.lower()
    assert "bollinger" in skill.lower()
    assert "stochastic" in skill.lower()
    assert "adx" in skill.lower()
    assert "stdev_sample" in skill
    assert "indicator_dataset_fingerprints" in skill
    assert "evaluation_start" in skill
    assert "do not edit" in skill.lower()
    assert "make run" in skill
    assert "Never deploys" in skill or "cannot deploy" in skill
    assert "--local" in skill
    assert "THYTRADER_API_BASE_URL" in skill or "loopback HTTP" in skill.lower()


def test_data_skill_requires_confirm_and_forbids_interpolation() -> None:
    """The data skill must gate ingest and deny trading plus interpolation."""
    skill = _DATA_SKILL.read_text(encoding="utf-8")
    assert "thytrader-data" in skill
    assert "--confirm" in skill
    assert "watch-add" in skill
    assert "inspect-gaps" in skill
    assert "Never interpolates" in skill or "never interpolated" in skill.lower()
    assert "Never deploys" in skill or "cannot deploy" in skill.lower()
    assert "202" in skill
    assert "market-data worker" in skill.lower() or "thytrader-market-data-worker" in skill
    assert "1m" in skill
    assert "2h" in skill
    assert "4h" in skill
    assert "per-indicator" in skill
    assert "do not edit" in skill.lower()
    assert "make run" in skill


def test_runtime_skill_requires_confirm_and_live_ack() -> None:
    """Runtime control must be confirmation-gated and distinct from operator/research."""
    skill = _RUNTIME_SKILL.read_text(encoding="utf-8")
    assert "thytrader-runtime" in skill
    assert "--confirm" in skill
    assert "--i-understand-live" in skill
    assert "start" in skill
    assert "pause" in skill
    assert "/api/v1/deployments" in skill
    assert "show-risk-policy" in skill
    assert "set-risk-policy" in skill
    assert "--daily-loss-limit-fraction" in skill
    assert "--max-strategy-drawdown-fraction" in skill
    assert "--reference-price-collar-fraction" in skill
    assert "/api/v1/risk-policy" in skill
    assert "place-order" in skill
    assert "/api/v1/discretionary-orders" in skill
    assert "--timeframe" in skill
    assert "--side" in skill
    assert "short" in skill.lower()
    assert "attached" in skill.lower()
    assert "per-indicator" in skill
    assert "not an extension" in skill.lower() or "not the operator" in skill.lower()
    assert "do not edit" in skill.lower()
    assert "make run" in skill
    assert "YOLO" in skill or "yolo" in skill
    assert "live" in skill.lower()
    assert "--maker-fee-rate" in skill
    assert "--taker-fee-rate" in skill
    assert "0.001" in skill
    assert "0.002" in skill
    assert "/api/v1/operator-chat" in skill
    assert "maker_fee_rate" in skill
    assert "taker_fee_rate" in skill
    assert "0050-daily-loss-drawdown-rate-collars" in skill
    assert "show-settings" in skill
    assert "set-settings" in skill
    assert "/api/v1/settings" in skill
    assert "0055-yaml-settings-runtime-reloadable-yolo" in skill
    assert "THYTRADER_YOLO_TIERS=paper" in skill
    assert "thytrader.yaml" in skill
    assert "show-coinbase-credentials" in skill
    assert "set-coinbase-credentials" in skill
    assert "clear-coinbase-credentials" in skill
    assert "--private-key-file" in skill
    assert "/api/v1/credentials/coinbase" in skill
    assert "YOLO never covers credentials" in skill
    assert "0053-workstation-ia-write-only-coinbase-credentials" in skill


def test_playbook_skill_sequences_lanes_without_live_authority() -> None:
    """The playbook skill must call existing CLIs, keep --confirm default, and forbid live."""
    skill = _PLAYBOOK_SKILL.read_text(encoding="utf-8")
    assert "thytrader-playbook" in skill
    assert "--confirm" in skill
    assert "thytrader-data" in skill
    assert "thytrader-research" in skill
    assert "thytrader-runtime" in skill
    assert "YOLO" in skill or "yolo" in skill
    assert "per-indicator" in skill
    assert "do not edit" in skill.lower()
    assert "make run" in skill
    assert "never" in skill.lower() and "live" in skill.lower()
    assert "--i-understand-live" in skill
    assert "not an extension" in skill.lower() or "does not grant live" in skill.lower()
    assert "0.001" in skill
    assert "0.002" in skill
    assert "thytrader.yaml" in skill
    assert "THYTRADER_YOLO_TIERS=paper" in skill


def test_memory_skill_requires_confirm_and_forbids_yolo() -> None:
    """The memory skill must gate mutations and deny YOLO plus trading."""
    skill = _MEMORY_SKILL.read_text(encoding="utf-8")
    assert "thytrader-memory" in skill
    assert "--confirm" in skill
    assert "YOLO never" in skill or "yolo never" in skill.lower()
    assert "add-journal" in skill
    assert "list-trade-reasons" in skill
    assert "add-trade-reason-note" in skill
    assert "notify" in skill
    assert "/api/v1/memory" in skill
    assert "train" in skill
    assert "list-models" in skill
    assert "show-model" in skill
    assert "/api/v1/memory/models" in skill
    assert "/api/v1/operator-chat" in skill
    assert "do not edit" in skill.lower()
    assert "make run" in skill
    assert "Never deploys" in skill or ("does not" in skill.lower() and "order" in skill.lower())


def test_committed_json_schema_matches_envelope_contract() -> None:
    """The shipped JSON Schema must keep the v1 envelope required keys."""
    schema_path = (
        _ROOT / "skills" / "thytrader-operator" / "references" / "operator-report-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema["required"])
    envelope_required = set(OperatorEnvelope.model_json_schema()["required"])
    assert envelope_required <= required
    assert "report_kind" in required
    assert "payload" in required
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert set(schema["properties"]["report_kind"]["enum"]) == set(REPORT_KINDS)
