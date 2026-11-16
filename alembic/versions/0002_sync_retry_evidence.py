"""Persist retry evidence for provider synchronizations."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sync_runs", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_runs", sa.Column("retry_log_json", sa.Text(), nullable=False, server_default="[]")
    )


def downgrade():
    op.drop_column("sync_runs", "retry_log_json")
    op.drop_column("sync_runs", "retry_count")
