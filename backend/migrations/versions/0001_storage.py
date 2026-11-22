"""Create demand history, prediction, and decision log tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demand_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("station_id", sa.String(100), nullable=False),
        sa.Column("fuel_type", sa.String(20), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("sim_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("demand_liters", sa.Float(), nullable=False),
        sa.Column("served_liters", sa.Float(), nullable=False),
        sa.Column("unmet_liters", sa.Float(), nullable=False),
        sa.UniqueConstraint("run_id", "source_id", name="uq_demand_run_source"),
    )
    op.create_index(
        "ix_demand_station_fuel_tick", "demand_observations",
        ["run_id", "station_id", "fuel_type", "tick"],
    )
    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("station_id", sa.String(100), nullable=False),
        sa.Column("fuel_type", sa.String(20), nullable=False),
        sa.Column("source_tick", sa.Integer(), nullable=False),
        sa.Column("target_tick", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("target_tick >= source_tick", name="ck_prediction_tick_order"),
    )
    op.create_index(
        "ix_prediction_lookup", "predictions", ["run_id", "station_id", "fuel_type", "target_tick"],
    )
    op.create_table(
        "decision_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("decision_key", sa.String(150), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("explanation", sa.String(), nullable=False),
        sa.Column("allocation_id", sa.Integer(), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_decision_run_tick", "decision_logs", ["run_id", "tick", "id"])
    op.create_index("ix_decision_run_key", "decision_logs", ["run_id", "decision_key"])


def downgrade() -> None:
    op.drop_table("decision_logs")
    op.drop_table("predictions")
    op.drop_table("demand_observations")
