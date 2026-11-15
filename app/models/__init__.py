"""Persisted entities for the CheckStock inventory domain."""

from datetime import datetime
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String,
    UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("store_id", "code", name="uq_warehouse_store_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "sku", name="uq_product_store_sku"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(220), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StockBalance(Base):
    __tablename__ = "stock_balances"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "product_id", name="uq_balance_warehouse_product"),
        CheckConstraint("on_hand >= 0", name="ck_balance_nonnegative"),
        CheckConstraint("reserved >= 0", name="ck_reservation_nonnegative"),
        CheckConstraint("reserved <= on_hand", name="ck_reserved_not_more_than_on_hand"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_transfer_positive"),
        CheckConstraint("source_warehouse_id <> destination_warehouse_id", name="ck_transfer_different_warehouses"),
        CheckConstraint("status IN ('in_transit', 'received', 'cancelled')", name="ck_transfer_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    destination_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="in_transit", server_default="in_transit", nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FBSReservation(Base):
    __tablename__ = "fbs_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_fbs_reservation_positive"),
        CheckConstraint("status IN ('active', 'cancelled', 'shipped')", name="ck_fbs_reservation_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    order_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_shipment_positive"),
        CheckConstraint("shipment_type IN ('FBS', 'FBO')", name="ck_shipment_type"),
        CheckConstraint("status IN ('in_transit', 'received')", name="ck_shipment_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_type: Mapped[str] = mapped_column(String(3), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    fbs_reservation_id: Mapped[int | None] = mapped_column(ForeignKey("fbs_reservations.id", ondelete="RESTRICT"), unique=True, nullable=True)
    destination: Mapped[str | None] = mapped_column(String(180), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="in_transit", server_default="in_transit", nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_movement_positive"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    destination_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
