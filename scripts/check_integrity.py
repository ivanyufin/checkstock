"""Read-only inventory conservation audit for a stable CheckStock database.

Run: docker compose exec web python -m scripts.check_integrity
No modifications are performed. Avoid running during concurrent writes, because
READ COMMITTED snapshots of separate queries could briefly differ.
"""
import asyncio
from collections import defaultdict

from sqlalchemy import func, select

from app.database import AsyncSessionLocal, engine
from app.inventory_read import inventory_snapshot
from app.models import FBSReservation, StockBalance, StockMovement


async def check() -> bool:
    async with AsyncSessionLocal() as session:
        snapshot = await inventory_snapshot(session)
        received_rows = (await session.execute(
            select(StockMovement.product_id, func.sum(StockMovement.quantity))
            .where(StockMovement.kind == "receipt")
            .group_by(StockMovement.product_id)
        )).all()
        received = dict(received_rows)

        active_rows = (await session.execute(
            select(FBSReservation.product_id, FBSReservation.warehouse_id,
                   func.sum(FBSReservation.quantity))
            .where(FBSReservation.status == "active")
            .group_by(FBSReservation.product_id, FBSReservation.warehouse_id)
        )).all()
        reserved_from_orders = {(pid, wid): int(q) for pid, wid, q in active_rows}

        balance_rows = (await session.execute(select(StockBalance))).scalars().all()
        errors = []
        all_pairs = set(reserved_from_orders)
        for balance in balance_rows:
            pair = (balance.product_id, balance.warehouse_id)
            all_pairs.add(pair)
            actual_reserved = reserved_from_orders.get(pair, 0)
            if balance.reserved != actual_reserved:
                errors.append(
                    f"Product #{balance.product_id} / FF #{balance.warehouse_id}: "
                    f"reserved={balance.reserved}, active FBS={actual_reserved}"
                )
            if not 0 <= balance.reserved <= balance.on_hand:
                errors.append(f"Invalid stock balance for product #{balance.product_id} / FF #{balance.warehouse_id}")

        recorded_pairs = {(b.product_id, b.warehouse_id) for b in balance_rows}
        for pid, wid in (all_pairs - recorded_pairs):
            errors.append(f"Active FBS reserve missing StockBalance for product #{pid} / FF #{wid}")

        print("SKU        RECEIPTS  ON_FF  FF_TRANSIT  FBS_TRANSIT  FBO_TRANSIT  FBO_RECEIVED  MATCH")
        for item in snapshot["items"]:
            incoming = int(received.get(item["id"], 0))
            accounted = (
                item["on_hand"]
                + item["transit_between_warehouses"]
                + item["transit_to_customer_fbs"]
                + item["transit_to_marketplace_fbo"]
                + item["received_on_marketplace_fbo"]
            )
            valid = incoming == accounted
            print(f"{item['sku']:<10} {incoming:>8} {item['on_hand']:>6} "
                  f"{item['transit_between_warehouses']:>11} {item['transit_to_customer_fbs']:>12} "
                  f"{item['transit_to_marketplace_fbo']:>12} {item['received_on_marketplace_fbo']:>13} "
                  f"{'OK' if valid else 'FAIL'}")
            if not valid:
                errors.append(f"{item['sku']}: receipts={incoming}, accounted={accounted}")

        if errors:
            print("\nFAIL: discrepancies detected:")
            for error in errors:
                print(" -", error)
            return False
        print("\nOK: stock conservation and FBS reservations are consistent.")
        return True


async def main() -> int:
    try:
        return 0 if await check() else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
