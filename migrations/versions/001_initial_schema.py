"""Initial warehouse schema with inventory invariants.

Revision ID: 001_initial_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("store_id", "code", name="uq_warehouse_store_code"),
    )
    op.create_index("ix_warehouses_store_id", "warehouses", ["store_id"])
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("name", sa.String(220), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("store_id", "sku", name="uq_product_store_sku"),
    )
    op.create_index("ix_products_store_id", "products", ["store_id"])
    op.create_table(
        "stock_balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("warehouse_id", "product_id", name="uq_balance_warehouse_product"),
        sa.CheckConstraint("on_hand >= 0", name="ck_balance_nonnegative"),
        sa.CheckConstraint("reserved >= 0", name="ck_reservation_nonnegative"),
        sa.CheckConstraint("reserved <= on_hand", name="ck_reserved_not_more_than_on_hand"),
    )
    op.create_index("ix_stock_balances_warehouse_id", "stock_balances", ["warehouse_id"])
    op.create_index("ix_stock_balances_product_id", "stock_balances", ["product_id"])
    op.create_table(
        "transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("destination_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_transit"),
        sa.Column("request_key", sa.String(100), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_transfer_positive"),
        sa.CheckConstraint("source_warehouse_id <> destination_warehouse_id", name="ck_transfer_different_warehouses"),
        sa.CheckConstraint("status IN ('in_transit', 'received')", name="ck_transfer_status"),
    )
    op.create_index("ix_transfers_product_id", "transfers", ["product_id"])
    op.create_table(
        "fbs_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("order_number", sa.String(100), nullable=True),
        sa.Column("request_key", sa.String(100), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_fbs_reservation_positive"),
        sa.CheckConstraint("status IN ('active', 'cancelled', 'shipped')", name="ck_fbs_reservation_status"),
    )
    op.create_index("ix_fbs_reservations_product_id", "fbs_reservations", ["product_id"])
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipment_type", sa.String(3), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fbs_reservation_id", sa.Integer(), sa.ForeignKey("fbs_reservations.id", ondelete="RESTRICT"), unique=True, nullable=True),
        sa.Column("destination", sa.String(180), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_transit"),
        sa.Column("request_key", sa.String(100), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_shipment_positive"),
        sa.CheckConstraint("shipment_type IN ('FBS', 'FBO')", name="ck_shipment_type"),
        sa.CheckConstraint("status IN ('in_transit', 'received')", name="ck_shipment_status"),
    )
    op.create_index("ix_shipments_product_id", "shipments", ["product_id"])
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("destination_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=True),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("request_key", sa.String(100), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0", name="ck_movement_positive"),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])


def downgrade() -> None:
    op.drop_table("stock_movements")
    op.drop_table("shipments")
    op.drop_table("fbs_reservations")
    op.drop_table("transfers")
    op.drop_table("stock_balances")
    op.drop_table("products")
    op.drop_table("warehouses")
    op.drop_table("stores")
