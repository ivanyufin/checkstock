"""Support cancellation of in-transit warehouse transfers.

Revision ID: 002_transfer_cancellation
Revises: 001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "002_transfer_cancellation"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_transfer_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfer_status", "transfers", "status IN ('in_transit', 'received', 'cancelled')")
    op.add_column("transfers", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Convert cancelled historical transfers to in_transit only for rollback.
    # Rollbacks should be avoided if cancelled rows exist, as they would appear in transit.
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT count(*) FROM transfers WHERE status='cancelled'")).scalar_one()
    if count:
        raise RuntimeError("Downgrade blocked: cancelled transfers exist. Preserve audit history.")
    op.drop_column("transfers", "cancelled_at")
    op.drop_constraint("ck_transfer_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfer_status", "transfers", "status IN ('in_transit', 'received')")
