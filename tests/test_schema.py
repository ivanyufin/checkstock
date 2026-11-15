from sqlalchemy import CheckConstraint, UniqueConstraint
from app.models import Base, StockBalance


def test_required_tables_present():
    expected = {"stores", "warehouses", "products", "stock_balances", "stock_movements", "transfers", "fbs_reservations", "shipments"}
    assert expected <= set(Base.metadata.tables)


def test_balances_have_invariants():
    constraints = StockBalance.__table__.constraints
    check_names = {c.name for c in constraints if isinstance(c, CheckConstraint)}
    unique_names = {c.name for c in constraints if isinstance(c, UniqueConstraint)}
    assert "ck_balance_nonnegative" in check_names
    assert "ck_reservation_nonnegative" in check_names
    assert "ck_reserved_not_more_than_on_hand" in check_names
    assert "uq_balance_warehouse_product" in unique_names
