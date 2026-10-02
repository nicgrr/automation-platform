"""make direct sales inventory-backed and add customer identity

Revision ID: 20261002_08
Revises: 20261002_07
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_08"
down_revision: Union[str, None] = "20261002_07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


sale_status = sa.Enum("COMPLETED", "VOIDED", name="salestatus")


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.add_column(sa.Column("email", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("phone", sa.String(length=64), nullable=True))
        batch.create_index("ix_customers_email", ["email"], unique=True)

    with op.batch_alter_table("sales") as batch:
        batch.add_column(sa.Column("submission_key", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("status", sale_status, nullable=False, server_default="COMPLETED"))
        batch.add_column(sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_sales_submission_key", ["submission_key"], unique=True)
        batch.create_index("ix_sales_source", ["source"])
        batch.create_index("ix_sales_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("sales") as batch:
        batch.drop_index("ix_sales_status")
        batch.drop_index("ix_sales_source")
        batch.drop_index("ix_sales_submission_key")
        batch.drop_column("voided_at")
        batch.drop_column("status")
        batch.drop_column("source")
        batch.drop_column("submission_key")

    with op.batch_alter_table("customers") as batch:
        batch.drop_index("ix_customers_email")
        batch.drop_column("phone")
        batch.drop_column("email")
