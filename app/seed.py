"""One-time demo receipts. Restarting the service must not replenish stock."""
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal, engine
from app.models import Product, StockBalance, StockMovement, Store, Warehouse
from app.demo_data import DEMO


async def seed_demo() -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            existing = await session.scalar(select(Store.id).where(Store.code == "demo"))
            if existing is not None:
                print("Demo store exists: skipping initial receipt.")
                return

            store = Store(code="demo", name="Демонстрационный магазин")
            session.add(store)
            await session.flush()

            north = Warehouse(store_id=store.id, code="NORTH", name="Север")
            south = Warehouse(store_id=store.id, code="SOUTH", name="Юг")
            session.add_all([north, south])
            await session.flush()

            for sku, (name, north_qty, south_qty) in DEMO.items():
                product = Product(store_id=store.id, sku=sku, name=name)
                session.add(product)
                await session.flush()

                for warehouse, qty in [(north, north_qty), (south, south_qty)]:
                    session.add(StockBalance(product_id=product.id, warehouse_id=warehouse.id, on_hand=qty, reserved=0))
                    if qty > 0:
                        session.add(StockMovement(
                            product_id=product.id,
                            destination_warehouse_id=warehouse.id,
                            kind="receipt",
                            quantity=qty,
                            note="Исходный тестовый приход",
                            request_key=f"demo-receipt:{sku}:{warehouse.code}",
                        ))
    print("Demo store and opening receipts created.")


async def main() -> None:
    try:
        await seed_demo()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
