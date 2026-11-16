"""Snapshot high water mark and source/attempt uniqueness."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reconciliation_runs", sa.Column("sync_high_water_mark", sa.String(64), nullable=True)
    )
    op.create_unique_constraint(
        "uq_source_batch_row", "source_rows", ["import_batch_id", "row_number"]
    )
    op.create_unique_constraint(
        "uq_run_attempt_number", "run_attempts", ["run_id", "attempt_number"]
    )
    op.drop_constraint("uq_finding_run_rule_entity", "findings", type_="unique")
    op.create_unique_constraint(
        "uq_finding_run_rule_entity",
        "findings",
        ["run_id", "rule_code", "entity_type", "entity_id"],
    )


def downgrade():
    op.drop_constraint("uq_finding_run_rule_entity", "findings", type_="unique")
    op.create_unique_constraint(
        "uq_finding_run_rule_entity", "findings", ["run_id", "rule_code", "entity_id"]
    )
    op.drop_constraint("uq_run_attempt_number", "run_attempts", type_="unique")
    op.drop_constraint("uq_source_batch_row", "source_rows", type_="unique")
    op.drop_column("reconciliation_runs", "sync_high_water_mark")
