"""Durable operator safety alerts for pause, protection, and worker supervision.

Revision ID: 0066
Revises: risk0065

Follows retained paper risk evidence; the integrated release head is 0068.
"""

import sqlalchemy as sa

from alembic import op

revision = "0066"
down_revision = "risk0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the append-only, deduplicated operator alert feed."""
    op.create_table(
        "operator_alert_checks",
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("code", "subject"),
    )
    op.create_table(
        "operator_alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=True),
        sa.Column("product_id", sa.String(length=32), nullable=True),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_detail", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("delivery_provider", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column(
            "delivery_status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_detail", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("delivery_token", sa.UUID(), nullable=True),
        sa.Column("delivery_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="SET NULL",
            name="fk_operator_alerts_deployment_id",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="ck_operator_alerts_severity"
        ),
        sa.CheckConstraint(
            "delivery_status IN "
            "('pending', 'skipped', 'logged', 'delivered', 'failed', 'exhausted')",
            name="ck_operator_alerts_delivery_status",
        ),
        sa.CheckConstraint("occurrences >= 1", name="ck_operator_alerts_occurrences_positive"),
        sa.PrimaryKeyConstraint("id"),
        comment="Durable deduplicated operator safety alerts (ADR 0115).",
    )
    op.create_index(
        "ix_operator_alerts_last_seen",
        "operator_alerts",
        ["last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ux_operator_alerts_open",
        "operator_alerts",
        ["code", "subject"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    """Remove the alert feed; supervision degrades to no durable alerts."""
    op.drop_index("ux_operator_alerts_open", table_name="operator_alerts")
    op.drop_index("ix_operator_alerts_last_seen", table_name="operator_alerts")
    op.drop_table("operator_alerts")
    op.drop_table("operator_alert_checks")
