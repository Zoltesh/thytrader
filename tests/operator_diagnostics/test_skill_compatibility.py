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
_PORTFOLIO_SKILL = _ROOT / "skills" / "thytrader-portfolio" / "SKILL.md"


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
        "/portfolio",
        "/fees",
        "/decisions",
        "/portfolios",
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
    assert "allow_intra_strategy_pyramiding" in schemas
    assert "multi_instrument_documents" in schemas
    assert "intra_strategy_pyramiding" in schemas
    assert "books" in schemas
    assert "protection_status" in schemas
    assert "0060-multi-book-deployment-api" in skill or "0060-multi-book-deployment-api" in schemas
    assert "thytrader-ops-contract-v57" in skill
    assert "0059" in skill
    assert "backtest_engine" in skill
    assert "thytrader-backtest" in skill
    assert "strategy_model" in skill
    assert "spot_quote_currencies" in skill or "USDC" in skill
    assert "spot_quote_currencies" in schemas
    assert "catalog_health" in schemas
    assert "0058-protection-lifecycle-accounting" in skill
    assert "lifecycle_command" in skill
    assert "lifecycle_commands" in schemas
    assert "worker_lease_held" in skill
    assert "revision" in skill
    assert "DAILY_LOSS_LIMIT" in schemas
    assert "STRATEGY_DRAWDOWN_LIMIT" in schemas
    assert "risk_breakers" in schemas
    assert "experiential_model_engines" in schemas
    assert "trade_reason_journals" in schemas
    assert "decision_journals" in schemas
    assert "thytrader-bar-decision-v1" in schemas
    assert "thytrader-operator decisions" in skill


def test_research_skill_requires_confirm_and_forbids_trading() -> None:
    """The research skill must gate mutations and deny paper/live control."""
    skill = _RESEARCH_SKILL.read_text(encoding="utf-8")
    assert "thytrader-research" in skill
    assert "--confirm" in skill
    assert "create-strategy" in skill
    assert "save-strategy" in skill
    assert "--revision" in skill
    assert "strategy_revision_conflict" in skill
    assert "import-strategy" in skill
    assert "clone-strategy" in skill
    assert "delete-strategy" in skill
    assert "bulk-delete-strategies" in skill
    assert "--dry-run" in skill
    assert "strategy_has_active_deployments" in skill
    assert "show-snapshot" in skill
    assert "--strategy-id" in skill
    assert "strategy_invalid" in skill
    for removed in ("create-draft", "import-draft", "save-draft", "publish --strategy-id"):
        assert removed not in skill, removed
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
    assert "backtest-model" in skill
    assert "engine-support" not in skill
    assert "spread_bps" in skill
    assert "validity_limits" in skill
    assert "engine_contract_version" in skill and "rejected" in skill
    assert "--template" in skill
    assert "macd" in skill.lower()
    assert "bollinger" in skill.lower()
    assert "stochastic" in skill.lower()
    assert "adx" in skill.lower()
    assert "stdev_sample" in skill
    assert "indicator_dataset_fingerprints" in skill
    assert "additional_instrument_datasets" in skill
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
    assert "--disabled" in skill
    assert "inspect-gaps" in skill
    assert "truncated" in skill
    assert "0072" in skill
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
    assert "start --strategy-id" in skill
    assert "start --strategy-fingerprint" not in skill
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
    assert "thytrader-runtime decisions" in skill
    assert "/api/v1/deployments/{deployment_id}/decisions" in skill
    assert "/api/v1/strategies/{strategy_id}/decisions" in skill
    assert "next_cursor" in skill
    assert "THYTRADER_YOLO_TIERS=paper" in skill
    assert "thytrader.yaml" in skill
    assert "show-coinbase-credentials" in skill
    assert "set-coinbase-credentials" in skill
    assert "clear-coinbase-credentials" in skill
    assert "--private-key-file" in skill
    assert "/api/v1/credentials/coinbase" in skill
    assert "YOLO never covers credentials" in skill
    assert "0053-workstation-ia-write-only-coinbase-credentials" in skill
    assert "--allow-intra-strategy-pyramiding" in skill
    assert "PYRAMIDING_NOT_ALLOWED" in skill
    assert "0060-multi-book-deployment-api" in skill
    assert "0065-deployment-capital-accounting-http" in skill or '"capital"' in skill
    assert "instrument_runtimes" in skill
    assert "book_totals" in skill
    assert "compatibility" in skill.lower()
    assert "--flatten" in skill
    assert "reset-breaker-latches" in skill
    assert "0064-deployment-http-lifecycle-and-breaker-latch-reset" in skill
    assert "managed shutdown" in skill.lower() or "managed-shutdown" in skill.lower()
    assert "0058-protection-lifecycle-accounting" in skill
    assert "lifecycle_command" in skill
    assert "allocated_capital" in skill
    assert "allocated capital" in skill.lower() or "venue available quote" in skill.lower()
    assert "worker_lease_held" in skill or "fenced lease" in skill.lower()


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
    assert "--create-strategy" in skill
    assert "--strategy-id" in skill
    assert "--create-draft" not in skill
    assert "--publish" not in skill


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


def test_portfolio_skill_requires_confirm_and_denies_deployment() -> None:
    """The portfolio lane is confirmation-gated, names every command, and cannot deploy."""
    skill = _PORTFOLIO_SKILL.read_text(encoding="utf-8")
    assert "thytrader-portfolio" in skill
    assert "--confirm" in skill
    assert "YOLO never" in skill
    for command in (
        "list",
        "show",
        "create",
        "update",
        "add-sleeve",
        "remove-sleeve",
        "set-weights",
        "backtest",
        "show-backtest",
        "list-backtests",
        "journal",
        "deployment",
        "briefing",
        "propose",
        "proposals",
        "show-proposal",
        "approve",
        "decline",
    ):
        assert f"thytrader-portfolio {command}" in skill, command
    for phrase in (
        "Acting as the manager agent",
        "Never place orders",
        "Never decide your own proposals",
        "Cadence",
        "Permission semantics",
        "thytrader-portfolio-briefing-v1",
        "drawdown_vs_backtest",
        "portfolio_proposal_not_permitted",
        "portfolio_proposal_not_pending",
        "thytrader-runtime portfolio-",
        "0091-portfolio-deployment-limits-and-manager-proposals",
    ):
        assert phrase in skill, phrase
    for code in (
        "portfolio_revision_conflict",
        "portfolio_allocation_exceeded",
        "portfolio_sleeve_quote_mismatch",
        "portfolio_backtest_rejected",
    ):
        assert code in skill, code
    assert "/api/v1/portfolios" in skill
    assert "thytrader-portfolio-backtest-v1" in skill
    assert "not simulated" in skill
    assert "cannot deploy" in skill.lower()
    assert "do not edit" in skill.lower()
    assert "0088-portfolio-model-and-portfolio-backtest" in skill
    assert (_ROOT / "ops" / ".cursor" / "skills" / "thytrader-portfolio").resolve() == (
        _PORTFOLIO_SKILL.parent.resolve()
    )


def test_runtime_skill_deploys_portfolios_with_the_same_gates() -> None:
    """The runtime lane names every portfolio command, the gates, and the breaker codes."""
    skill = (_ROOT / "skills" / "thytrader-runtime" / "SKILL.md").read_text(encoding="utf-8")
    for command in (
        "portfolio-status",
        "portfolio-start",
        "portfolio-pause",
        "portfolio-resume",
        "portfolio-stop",
        "portfolio-reset-breaker",
    ):
        assert f"thytrader-runtime {command}" in skill, command
    for phrase in (
        "--i-understand-live",
        "portfolio_start_rejected",
        "strategy_busy",
        "allocation membership",
        "PORTFOLIO_TOTAL_EXPOSURE_LIMIT",
        "PORTFOLIO_DRAWDOWN_STOP",
        "PORTFOLIO_BREAKER_LATCHED",
        "YOLO never",
    ):
        assert phrase in skill, phrase


def test_skills_teach_signal_exits_end_to_end() -> None:
    """Operator agents can author, research, run, and read signal exits from skills alone."""
    research = _RESEARCH_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_SKILL.read_text(encoding="utf-8")
    operator = _OPERATOR_SKILL.read_text(encoding="utf-8")
    schemas = _SCHEMAS.read_text(encoding="utf-8")
    for needle in (
        "exits.signal_exit",
        "crosses_below",
        "ema-trend-hold",
        "signal_exit_at_close",
        "exit_reasons",
        "exit_condition",
        "0093-signal-based-exits",
    ):
        assert needle in research, needle
    for needle in ("signal_exit_bar", "exit_reason:", "EXIT_SIGNAL", "exit_rule", "pending_exit"):
        assert needle in runtime, needle
    assert "signal_exit_runtimes" in operator
    assert "`signal`" in operator
    assert "EXIT_SIGNAL" in schemas
    assert "exit_rule" in schemas
    assert "signal_exit_runtimes" in schemas


def test_skills_document_sparse_market_coverage() -> None:
    """Operators can judge a thin market's series from skills alone (ADR 0095)."""
    data = _DATA_SKILL.read_text(encoding="utf-8")
    operator = _OPERATOR_SKILL.read_text(encoding="utf-8")
    schemas = _SCHEMAS.read_text(encoding="utf-8")
    research = _RESEARCH_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_SKILL.read_text(encoding="utf-8")
    assert "0095-sparse-markets-no-trade-bars-listing-floors" in data
    assert "Check that a series is healthy" in data
    for field in ("watch_covered_candle_count", "island_complete", "synthetic_no_trade_intervals"):
        assert field in data
        assert field in schemas
    assert "listing search" in data
    assert "0059" in data
    assert "watch_relative_complete" in operator
    assert "watch_coverage_ratio" in schemas
    assert "no_trade_bar" in schemas
    assert "synthetic_no_trade_bars" in research
    assert "no_trade_bar" in runtime
    assert "data_gap" in runtime


def test_skills_teach_reference_instruments_end_to_end() -> None:
    """Operator agents can author, research, run, and read reference instruments (ADR 0096)."""
    research = _RESEARCH_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_SKILL.read_text(encoding="utf-8")
    operator = _OPERATOR_SKILL.read_text(encoding="utf-8")
    schemas = _SCHEMAS.read_text(encoding="utf-8")
    for needle in (
        "data_requirements.reference_instruments",
        '"source": "btc"',
        "btc-regime-gate",
        "reference_dataset_fingerprints",
        "role: decision|filter|indicator|reference",
        "--reference-dataset-fingerprint",
        "BTC stays BTC",
        "0096-reference-instruments",
    ):
        assert needle in research, needle
    for needle in (
        "reference_data_stale",
        "reference_data_missing",
        "REFERENCE_DATA_STALE",
        "thytrader-data watch-add --product-id BTC-USDC --timeframe 1d",
        "BTC · EMA(100) [1d]",
    ):
        assert needle in runtime, needle
    for needle in (
        "reference_instrument_runtimes",
        "max_reference_instruments",
        "reference_data_stale",
    ):
        assert needle in operator, needle
    assert "reference_data_missing" in schemas


def test_skills_document_position_state_parity_and_fill_comparison() -> None:
    """Agents read open-and-protected vs exiting and twin fills from skills alone (ADR 0097)."""
    runtime = _RUNTIME_SKILL.read_text(encoding="utf-8")
    operator = _OPERATOR_SKILL.read_text(encoding="utf-8")
    schemas = _SCHEMAS.read_text(encoding="utf-8")
    portfolio = _PORTFOLIO_SKILL.read_text(encoding="utf-8")
    research = _RESEARCH_SKILL.read_text(encoding="utf-8")
    for needle in ("position_state", "open_protected", "exit_in_flight", "exiting"):
        assert needle in runtime, needle
        assert needle in schemas, needle
    assert "same_bar_exit_precedence" in operator
    assert "runtime_observability" in operator
    assert "paper_live_fill_comparisons" in schemas
    assert "paper_live_fill_comparisons" in portfolio
    assert "average_fill_vs_limit_bps" in portfolio
    assert "--submit-timeout-seconds" in research
    assert "0097-runtime-parity-and-observability" in runtime
