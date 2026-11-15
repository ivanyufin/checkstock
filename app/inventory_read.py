"""Read-only view model. Business operations will be implemented in services."""
from collections import defaultdict
from sqlalchemy import select, func
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Product, Warehouse, StockBalance, Transfer, Shipment, StockMovement


async def inventory_snapshot(session: AsyncSession) -> dict:
    products = (await session.scalars(select(Product).order_by(Product.sku))).all()
    warehouses = (await session.scalars(select(Warehouse).order_by(Warehouse.id))).all()
    balances = (await session.scalars(select(StockBalance))).all()
    by_pair = {(b.product_id, b.warehouse_id): b for b in balances}

    transfer_rows = (await session.execute(
        select(Transfer.product_id, func.sum(Transfer.quantity))
        .where(Transfer.status == "in_transit")
        .group_by(Transfer.product_id)
    )).all()
    transfer_qty = dict(transfer_rows)

    shipment_rows = (await session.execute(
        select(Shipment.product_id, Shipment.shipment_type, func.sum(Shipment.quantity))
        .where(Shipment.status == "in_transit")
        .group_by(Shipment.product_id, Shipment.shipment_type)
    )).all()
    shipments = {(pid, kind): qty for pid, kind, qty in shipment_rows}

    items = []
    for product in products:
        spots = []
        for warehouse in warehouses:
            # The test task describes one store, but keep identities scoped correctly.
            if product.store_id != warehouse.store_id:
                continue
            balance = by_pair.get((product.id, warehouse.id))
            on_hand = balance.on_hand if balance else 0
            reserved = balance.reserved if balance else 0
            spots.append({
                "warehouse_id": warehouse.id,
                "warehouse_name": warehouse.name,
                "warehouse_active": warehouse.is_active,
                "on_hand": on_hand,
                "reserved": reserved,
                "available": on_hand - reserved,
            })
        items.append({
            "id": product.id,
            "sku": product.sku,
            "name": product.name,
            "warehouses": spots,
            "on_hand": sum(s["on_hand"] for s in spots),
            "reserved": sum(s["reserved"] for s in spots),
            "available": sum(s["available"] for s in spots),
            "transit_between_warehouses": int(transfer_qty.get(product.id, 0)),
            "transit_to_marketplace_fbo": int(shipments.get((product.id, "FBO"), 0)),
            "transit_to_customer_fbs": int(shipments.get((product.id, "FBS"), 0)),
        })

    fbo_received_rows = (await session.execute(
        select(Shipment.product_id, func.sum(Shipment.quantity))
        .where(Shipment.shipment_type == "FBO", Shipment.status == "received")
        .group_by(Shipment.product_id)
    )).all()
    fbo_received = dict(fbo_received_rows)
    for item in items:
        item["received_on_marketplace_fbo"] = int(fbo_received.get(item["id"], 0))

    totals = {
        "on_hand": sum(item["on_hand"] for item in items),
        "reserved": sum(item["reserved"] for item in items),
        "available": sum(item["available"] for item in items),
        "transit_between_warehouses": sum(item["transit_between_warehouses"] for item in items),
        "transit_to_marketplace_fbo": sum(item["transit_to_marketplace_fbo"] for item in items),
        "transit_to_customer_fbs": sum(item["transit_to_customer_fbs"] for item in items),
        "received_on_marketplace_fbo": sum(item["received_on_marketplace_fbo"] for item in items),
    }
    return {"items": items, "totals": totals, "warehouses": [
        {"id": w.id, "name": w.name, "active": w.is_active} for w in warehouses
    ]}


async def recent_movements(session: AsyncSession, limit: int = 12) -> list[dict]:
    source_ff = aliased(Warehouse)
    destination_ff = aliased(Warehouse)
    rows = (await session.execute(
        select(StockMovement, Product.sku, source_ff.name, destination_ff.name)
        .join(Product, Product.id == StockMovement.product_id)
        .outerjoin(source_ff, source_ff.id == StockMovement.source_warehouse_id)
        .outerjoin(destination_ff, destination_ff.id == StockMovement.destination_warehouse_id)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .limit(limit)
    )).all()
    source_fallback = {"transfer_cancelled": "В пути между ФФ"}
    destination_fallback = {
        "fbs_shipped": "Клиент (FBS)",
        "fbo_shipped": "Маркетплейс (FBO)",
        "fbo_received": "Принято маркетплейсом",
    }
    return [{
        "id": movement.id,
        "sku": sku,
        "kind": movement.kind,
        "quantity": movement.quantity,
        "source": source_name or source_fallback.get(movement.kind, "Поставщик" if movement.kind == "receipt" else "—"),
        "destination": destination_name or destination_fallback.get(movement.kind, "—"),
        "source_warehouse_id": movement.source_warehouse_id,
        "destination_warehouse_id": movement.destination_warehouse_id,
        "reference_type": movement.reference_type,
        "reference_id": movement.reference_id,
        "note": movement.note,
        "created_at": movement.created_at.isoformat() if movement.created_at else None,
    } for movement, sku, source_name, destination_name in rows]
