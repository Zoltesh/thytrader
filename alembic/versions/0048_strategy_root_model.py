"""Strategy-root model: mutable strategies, automatic snapshots, strategy_id foreign keys.

Revision ID: 0048
Revises: 0047
Create Date: 2026-09-30

ADR 0082. This is a one-way, operator-approved data reshape:

* Creates ``strategies`` (one mutable row per strategy, revision-guarded),
  ``strategy_snapshots`` (content-addressed canonical definitions), and
  ``research_study_strategies`` (study membership).
* Drops ``strategy_drafts``, ``published_strategy_versions``, and
  ``archived_strategy_versions``.
* Wipes and recreates ``strategy_dataset_bindings``, ``published_research_run_specs``,
  ``published_backtest_results``, ``published_research_studies``, and
  ``research_jobs`` with ``strategy_id`` foreign keys (all rows were test data).
* Deletes every PAPER deployment with its intents, orders, fills, positions,
  instrument state, and trade reasons.
* Keeps every LIVE deployment with its ledger. The snapshot each one ran is copied
  into ``strategy_snapshots`` (``strategy_id`` NULL) after stripping the retired
  ``version``/``status`` keys, which re-addresses it; the deployment and its trade
  reasons are pointed at the new fingerprint, detached (``strategy_id`` NULL), and
  keep the strategy name in ``deployments.strategy_name``.
* Drops ``trade_reason_records.strategy_version``.
* Keeps the active risk policy. When it lists capital allocations (all of which
  referenced wiped strategies), publishes the next policy version without them and
  points the active row at it, exactly as ``PostgresRiskPolicyStore.publish`` does.
* Keeps market data, watchlist, feed state, portfolio snapshots, credentials, YAML
  settings, audit events, and experiential journals untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa

from alembic import op
from thytrader.risk.models import (
    canonical_risk_policy_bytes,
    definition_from_stored_json,
    risk_policy_fingerprint,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_FP = "'^sha256:[0-9a-f]{64}$'"
_TZ = sa.DateTime(timezone=True)
_LEGACY_KEYS = ("version", "status")
_PAPER_LEDGER_DELETES: tuple[str, ...] = (
    "UPDATE execution_orders SET parent_order_id = NULL"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
    "DELETE FROM trade_reason_records"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
    "DELETE FROM execution_fills"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
    "DELETE FROM execution_orders"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
    "DELETE FROM order_intents"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
    "DELETE FROM execution_positions"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
    "DELETE FROM execution_instrument_state"
    " WHERE deployment_id IN (SELECT id FROM deployments WHERE mode = 'paper')",
)


def upgrade() -> None:
    """Reshape strategy storage around one mutable root row per strategy."""
    connection = op.get_bind()
    _refuse_active_live_strategy_books(connection)
    _create_strategy_tables()
    op.drop_constraint("ck_deployments_kind_identity", "deployments", type_="check")
    op.drop_constraint("deployments_strategy_fingerprint_fkey", "deployments", type_="foreignkey")
    op.add_column(
        "deployments",
        sa.Column(
            "strategy_name",
            sa.String(120),
            nullable=True,
            comment="Strategy name captured at start; survives strategy deletion.",
        ),
    )
    _delete_paper_deployments(connection)
    _preserve_live_snapshots(connection)
    _drop_legacy_research_tables()
    _create_research_tables()
    _constrain_deployments()
    op.drop_column("trade_reason_records", "strategy_version")
    _drop_wiped_allocations(connection)


def _refuse_active_live_strategy_books(connection: Connection) -> None:
    """Fail closed: a running or paused live strategy book must be stopped first.

    Only stopped live deployments may be detached from their (wiped) strategy, so
    the migration never silently orphans a book that can still send real orders.
    """
    active = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM deployments WHERE mode = 'live' AND kind = 'strategy' "
            "AND status IN ('running', 'paused')"
        )
    ).scalar_one()
    if int(active) > 0:
        raise RuntimeError(
            "Stop every running or paused live strategy deployment before applying 0048."
        )


def downgrade() -> None:
    """Refuse: 0048 wipes research/paper data and cannot restore retired lifecycle rows."""
    raise RuntimeError("Revision 0048 is a one-way data reshape (ADR 0082).")


def _create_strategy_tables() -> None:
    """Create the mutable strategy root and its content-addressed snapshots."""
    op.create_table(
        "strategies",
        sa.Column(
            "strategy_id", sa.String(36), primary_key=True, comment="UUIDv7 strategy identity."
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "product_id", sa.String(32), nullable=True, comment="Primary product when parseable."
        ),
        sa.Column(
            "timeframe", sa.String(8), nullable=True, comment="Decision clock when parseable."
        ),
        sa.Column(
            "document", sa.Text(), nullable=False, comment="Canonical JSON when valid, else sorted."
        ),
        sa.Column("is_valid", sa.Boolean(), nullable=False),
        sa.Column("validation_issues", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("current_fingerprint", sa.String(71), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_strategies_revision_positive"),
        sa.CheckConstraint(
            "(is_valid AND current_fingerprint IS NOT NULL) "
            "OR (NOT is_valid AND current_fingerprint IS NULL)",
            name="ck_strategies_validity_fingerprint",
        ),
        sa.CheckConstraint(
            f"current_fingerprint IS NULL OR current_fingerprint ~ {_FP}",
            name="ck_strategies_current_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_strategies_updated",
        "strategies",
        [sa.text("updated_at DESC"), "strategy_id"],
    )
    op.create_table(
        "strategy_snapshots",
        sa.Column("strategy_fingerprint", sa.String(71), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.String(36),
            nullable=True,
            comment="Owning strategy; NULL only for snapshots a kept live deployment ran.",
        ),
        sa.Column("canonical_definition", sa.Text(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="SET NULL",
            name="fk_strategy_snapshots_strategy_id",
        ),
        sa.CheckConstraint(
            f"strategy_fingerprint ~ {_FP}",
            name="ck_strategy_snapshots_fingerprint_format",
        ),
    )
    op.create_index("ix_strategy_snapshots_strategy_id", "strategy_snapshots", ["strategy_id"])


def _delete_paper_deployments(connection: Connection) -> None:
    """Delete every paper deployment and its ledger rows (child tables first)."""
    for statement in _PAPER_LEDGER_DELETES:
        connection.execute(sa.text(statement))
    connection.execute(sa.text("DELETE FROM deployments WHERE mode = 'paper'"))


def _preserve_live_snapshots(connection: Connection) -> None:
    """Copy each kept live deployment's strategy into a re-addressed detached snapshot."""
    rows = connection.execute(
        sa.text(
            "SELECT DISTINCT p.strategy_fingerprint, p.canonical_definition, p.published_at "
            "FROM deployments d JOIN published_strategy_versions p "
            "ON p.strategy_fingerprint = d.strategy_fingerprint"
        )
    ).mappings()
    for row in rows:
        old_fingerprint = cast("str", row["strategy_fingerprint"])
        canonical, name = _strip_lifecycle_keys(cast("str", row["canonical_definition"]))
        new_fingerprint = "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()
        connection.execute(
            sa.text(
                "INSERT INTO strategy_snapshots "
                "(strategy_fingerprint, strategy_id, canonical_definition, created_at) "
                "VALUES (:fp, NULL, :doc, :at) ON CONFLICT (strategy_fingerprint) DO NOTHING"
            ),
            {"fp": new_fingerprint, "doc": canonical, "at": row["published_at"]},
        )
        connection.execute(
            sa.text(
                "UPDATE deployments SET strategy_fingerprint = :new, strategy_id = NULL, "
                "strategy_name = :name WHERE strategy_fingerprint = :old"
            ),
            {"new": new_fingerprint, "old": old_fingerprint, "name": name},
        )
        connection.execute(
            sa.text(
                "UPDATE trade_reason_records SET strategy_fingerprint = :new "
                "WHERE strategy_fingerprint = :old"
            ),
            {"new": new_fingerprint, "old": old_fingerprint},
        )
    connection.execute(sa.text("UPDATE deployments SET strategy_id = NULL WHERE kind = 'strategy'"))


def _strip_lifecycle_keys(canonical: str) -> tuple[str, str | None]:
    """Remove retired lifecycle keys and re-render canonical JSON (other keys unchanged)."""
    loaded: object = json.loads(canonical)
    if not isinstance(loaded, dict):
        raise TypeError("Stored strategy definition is not a JSON object.")
    document = {str(key): value for key, value in loaded.items() if key not in _LEGACY_KEYS}
    name = document.get("name")
    rendered = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered, name[:120] if isinstance(name, str) else None


def _drop_legacy_research_tables() -> None:
    """Drop retired lifecycle tables and the research tables that are recreated."""
    for table in (
        "research_jobs",
        "published_backtest_results",
        "published_research_run_specs",
        "strategy_dataset_bindings",
        "published_research_studies",
        "archived_strategy_versions",
        "strategy_drafts",
        "published_strategy_versions",
    ):
        op.drop_table(table)


def _create_research_tables() -> None:
    """Recreate research evidence tables with strategy_id foreign keys."""
    _create_bindings_and_runs()
    _create_results()
    _create_jobs()
    _create_studies()


def _strategy_fk(table: str) -> sa.ForeignKeyConstraint:
    """Return the cascading strategy_id foreign key for one research table."""
    return sa.ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name=f"fk_{table}_strategy_id",
    )


def _create_bindings_and_runs() -> None:
    """Create snapshot/dataset bindings and immutable research run specifications."""
    op.create_table(
        "strategy_dataset_bindings",
        sa.Column("strategy_fingerprint", sa.String(71), primary_key=True),
        sa.Column("dataset_fingerprint", sa.String(71), primary_key=True),
        sa.Column("strategy_id", sa.String(36), nullable=False),
        sa.Column("bound_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_fingerprint"],
            ["strategy_snapshots.strategy_fingerprint"],
            name="fk_strategy_dataset_bindings_snapshot",
        ),
        _strategy_fk("strategy_dataset_bindings"),
        sa.CheckConstraint(
            f"strategy_fingerprint ~ {_FP}",
            name="ck_strategy_dataset_binding_strategy_fingerprint_format",
        ),
        sa.CheckConstraint(
            f"dataset_fingerprint ~ {_FP}",
            name="ck_strategy_dataset_binding_dataset_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_strategy_dataset_bindings_dataset_fingerprint",
        "strategy_dataset_bindings",
        ["dataset_fingerprint"],
    )
    op.create_index(
        "ix_strategy_dataset_bindings_strategy_id", "strategy_dataset_bindings", ["strategy_id"]
    )
    op.create_table(
        "published_research_run_specs",
        sa.Column("run_fingerprint", sa.String(71), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False, unique=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("strategy_fingerprint", sa.String(71), nullable=False),
        sa.Column("strategy_id", sa.String(36), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(71), nullable=False),
        sa.Column("execution_fingerprint", sa.String(71), nullable=True),
        sa.Column("canonical_specification", sa.Text(), nullable=False),
        sa.Column("published_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_fingerprint", "dataset_fingerprint"],
            [
                "strategy_dataset_bindings.strategy_fingerprint",
                "strategy_dataset_bindings.dataset_fingerprint",
            ],
            name="fk_research_run_specs_binding",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="CASCADE",
            name="fk_research_run_specs_strategy_id",
        ),
        sa.CheckConstraint(f"run_fingerprint ~ {_FP}", name="ck_research_run_fingerprint_format"),
        sa.CheckConstraint(
            f"strategy_fingerprint ~ {_FP}",
            name="ck_research_run_strategy_fingerprint_format",
        ),
        sa.CheckConstraint(
            f"dataset_fingerprint ~ {_FP}",
            name="ck_research_run_dataset_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_published_research_run_specs_strategy_id",
        "published_research_run_specs",
        ["strategy_id"],
    )
    op.create_index(
        "ix_published_research_run_specs_dataset_fingerprint",
        "published_research_run_specs",
        ["dataset_fingerprint"],
    )
    op.create_index(
        "ux_published_research_run_specs_execution_fingerprint",
        "published_research_run_specs",
        ["execution_fingerprint"],
        unique=True,
        postgresql_where=sa.text("execution_fingerprint IS NOT NULL"),
    )


def _create_results() -> None:
    """Create immutable backtest results keyed by content fingerprint."""
    op.create_table(
        "published_backtest_results",
        sa.Column("result_fingerprint", sa.String(71), primary_key=True),
        sa.Column("run_fingerprint", sa.String(71), nullable=False),
        sa.Column("strategy_fingerprint", sa.String(71), nullable=False),
        sa.Column("strategy_id", sa.String(36), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(71), nullable=False),
        sa.Column("signal_trace_fingerprint", sa.String(71), nullable=False),
        sa.Column("canonical_result", sa.Text(), nullable=False),
        sa.Column("published_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["run_fingerprint"],
            ["published_research_run_specs.run_fingerprint"],
            name="fk_backtest_results_run",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="CASCADE",
            name="fk_backtest_results_strategy_id",
        ),
        sa.CheckConstraint(
            f"result_fingerprint ~ {_FP}", name="ck_backtest_result_fingerprint_format"
        ),
        sa.CheckConstraint(
            f"run_fingerprint ~ {_FP}", name="ck_backtest_result_run_fingerprint_format"
        ),
        sa.CheckConstraint(
            f"strategy_fingerprint ~ {_FP}",
            name="ck_backtest_result_strategy_fingerprint_format",
        ),
        sa.CheckConstraint(
            f"dataset_fingerprint ~ {_FP}",
            name="ck_backtest_result_dataset_fingerprint_format",
        ),
        sa.CheckConstraint(
            f"signal_trace_fingerprint ~ {_FP}",
            name="ck_backtest_result_signal_trace_fingerprint_format",
        ),
    )
    table = "published_backtest_results"
    op.create_index(
        "ix_published_backtest_results_run_published",
        table,
        ["run_fingerprint", sa.text("published_at DESC"), "result_fingerprint"],
    )
    op.create_index(
        "ix_published_backtest_results_strategy_published",
        table,
        ["strategy_fingerprint", sa.text("published_at DESC"), "result_fingerprint"],
    )
    op.create_index(
        "ix_published_backtest_results_strategy_id_published",
        table,
        ["strategy_id", sa.text("published_at DESC"), "result_fingerprint"],
    )
    op.create_index(
        "ix_published_backtest_results_dataset_fingerprint", table, ["dataset_fingerprint"]
    )
    op.create_index(
        "ix_published_backtest_results_published_result",
        table,
        [sa.text("published_at DESC"), "result_fingerprint"],
    )


def _create_jobs() -> None:
    """Create durable research jobs owned by one strategy."""
    op.create_table(
        "research_jobs",
        sa.Column("job_id", sa.UUID(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("strategy_id", sa.String(36), nullable=False),
        sa.Column("strategy_fingerprint", sa.String(71), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(256), nullable=True),
        sa.Column("failed_phase", sa.String(32), nullable=True),
        sa.Column("failed_detail", sa.String(500), nullable=True),
        sa.Column("run_fingerprint", sa.String(71), nullable=True),
        sa.Column("result_fingerprint", sa.String(71), nullable=True),
        sa.Column("study_fingerprint", sa.String(71), nullable=True),
        sa.Column("plan_fingerprint", sa.String(71), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"),
        _strategy_fk("research_jobs"),
        sa.CheckConstraint("kind IN ('backtest', 'study')", name="ck_research_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')",
            name="ck_research_jobs_status",
        ),
        sa.CheckConstraint("progress_current >= 0", name="ck_research_jobs_progress_current"),
        sa.CheckConstraint("progress_total >= 0", name="ck_research_jobs_progress_total"),
    )
    op.create_index("ix_research_jobs_status_created", "research_jobs", ["status", "created_at"])
    op.create_index(
        "ix_research_jobs_strategy_created",
        "research_jobs",
        ["strategy_id", sa.text("created_at DESC")],
    )


def _create_studies() -> None:
    """Create the persisted study catalog plus strategy membership links."""
    op.create_table(
        "published_research_studies",
        sa.Column("study_fingerprint", sa.String(71), primary_key=True),
        sa.Column(
            "strategy_id", sa.String(36), nullable=False, comment="Primary (first) strategy."
        ),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("plan_fingerprint", sa.String(71), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("engine_contract_version", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("window_count", sa.Integer(), nullable=False),
        sa.Column("selected_strategy_fingerprint", sa.String(71), nullable=True),
        sa.Column("mean_oos_return_fraction", sa.String(64), nullable=True),
        sa.Column("stitched_oos_available", sa.Boolean(), nullable=True),
        sa.Column("selection_metric", sa.String(64), nullable=True),
        sa.Column("canonical_study", sa.Text(), nullable=False),
        sa.Column("published_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="CASCADE",
            name="fk_research_studies_strategy_id",
        ),
        sa.CheckConstraint(
            f"study_fingerprint ~ {_FP}", name="ck_research_study_fingerprint_format"
        ),
        sa.CheckConstraint(
            f"request_fingerprint ~ {_FP}", name="ck_research_study_request_fingerprint_format"
        ),
        sa.CheckConstraint(
            f"plan_fingerprint ~ {_FP}", name="ck_research_study_plan_fingerprint_format"
        ),
        sa.CheckConstraint(
            "kind IN ("
            "'oos_holdout', 'walk_forward', 'cross_market', "
            "'parameter_sweep', 'walk_forward_optimization'"
            ")",
            name="ck_research_study_kind",
        ),
        sa.CheckConstraint(
            "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
            name="ck_research_study_timeframe",
        ),
        sa.CheckConstraint("window_count >= 1", name="ck_research_study_window_count"),
        sa.CheckConstraint(
            f"selected_strategy_fingerprint IS NULL OR selected_strategy_fingerprint ~ {_FP}",
            name="ck_research_study_selected_fingerprint_format",
        ),
    )
    table = "published_research_studies"
    op.create_index(
        "ix_published_research_studies_published_at_desc",
        table,
        [sa.text("published_at DESC"), "study_fingerprint"],
    )
    op.create_index(
        "ix_published_research_studies_kind_published",
        table,
        ["kind", sa.text("published_at DESC"), "study_fingerprint"],
    )
    op.create_index(
        "ix_published_research_studies_plan_fingerprint",
        table,
        ["plan_fingerprint"],
        unique=True,
    )
    op.create_index(
        "ix_published_research_studies_strategy_published",
        table,
        ["strategy_id", sa.text("published_at DESC")],
    )
    op.create_table(
        "research_study_strategies",
        sa.Column("study_fingerprint", sa.String(71), primary_key=True),
        sa.Column("strategy_id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(
            ["study_fingerprint"],
            ["published_research_studies.study_fingerprint"],
            ondelete="CASCADE",
            name="fk_research_study_strategies_study",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="CASCADE",
            name="fk_research_study_strategies_strategy_id",
        ),
    )
    op.create_index(
        "ix_research_study_strategies_strategy_id", "research_study_strategies", ["strategy_id"]
    )


def _constrain_deployments() -> None:
    """Point deployments at snapshots and strategies; only stopped live books may detach."""
    op.create_foreign_key(
        "fk_deployments_strategy_snapshot",
        "deployments",
        "strategy_snapshots",
        ["strategy_fingerprint"],
        ["strategy_fingerprint"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_deployments_strategy_id",
        "deployments",
        "strategies",
        ["strategy_id"],
        ["strategy_id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_deployments_kind_identity",
        "deployments",
        "("
        "kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND ("
        "strategy_id IS NOT NULL OR (mode = 'live' AND status = 'stopped'))"
        ") OR ("
        "kind = 'discretionary' AND strategy_fingerprint IS NULL AND strategy_id IS NULL "
        "AND timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')"
        ")",
    )


def _drop_wiped_allocations(connection: Connection) -> None:
    """Publish the next risk-policy version without allocations for wiped strategies."""
    row = (
        connection.execute(
            sa.text(
                "SELECT p.policy_id, p.version, p.canonical_definition "
                "FROM active_risk_policy a JOIN published_risk_policies p "
                "ON p.policy_fingerprint = a.policy_fingerprint WHERE a.id = 1"
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return
    current = definition_from_stored_json(cast("str", row["canonical_definition"]))
    if not current.allocations:
        return
    latest = connection.execute(
        sa.text("SELECT MAX(version) FROM published_risk_policies WHERE policy_id = :policy_id"),
        {"policy_id": row["policy_id"]},
    ).scalar_one()
    successor = type(current).model_validate(
        {
            **current.model_dump(mode="python"),
            "version": int(latest) + 1,
            "allocations": (),
        }
    )
    fingerprint = risk_policy_fingerprint(successor)
    now = datetime.now(UTC)
    connection.execute(
        sa.text(
            "INSERT INTO published_risk_policies "
            "(policy_fingerprint, policy_id, version, canonical_definition, published_at) "
            "VALUES (:fp, :policy_id, :version, :doc, :at) "
            "ON CONFLICT (policy_fingerprint) DO NOTHING"
        ),
        {
            "fp": fingerprint,
            "policy_id": str(successor.policy_id),
            "version": successor.version,
            "doc": canonical_risk_policy_bytes(successor).decode("utf-8"),
            "at": now,
        },
    )
    connection.execute(
        sa.text(
            "UPDATE active_risk_policy SET policy_fingerprint = :fp, activated_at = :at "
            "WHERE id = 1"
        ),
        {"fp": fingerprint, "at": now},
    )
