"""Atomic inventory state transitions backed by PostgreSQL row locks.

All domain mutations use one SQL transaction; no HTTP route may update a
StockBalance directly. A request_key protects create operations against retries.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FBSReservation, Product, Shipment, StockBalance, StockMovement,
    Store, Transfer, Warehouse,
)
from app.schemas.operations import FBOIn, ReceiptIn, ReserveIn, TransferIn


class InventoryError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def failure(message: str, code: int = 409):
    raise InventoryError(message, code)


async def lock_idempotency_key(session: AsyncSession, scope: str, key: str) -> None:
    # Same-key concurrent submissions serialize. hashtext collisions only
    # serialize unrelated operations; they cannot mix their records.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"{scope}:{key}"))))


def assert_same(existing: Any, **fields: Any) -> None:
    if any(getattr(existing, name) != value for name, value in fields.items()):
        failure("Этот request_key уже использован с другими параметрами")


async def product_exists(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        failure("Товар не найден", 404)
    return product


async def warehouse_locked(session: AsyncSession, warehouse_id: int, active: bool = True) -> Warehouse:
    warehouse = await session.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
    )
    if warehouse is None:
        failure("Склад не найден", 404)
    if active and not warehouse.is_active:
        failure("Склад архивирован, новые операции запрещены")
    return warehouse


async def pair_check(session: AsyncSession, product_id: int, *warehouse_ids: int, active: bool = True):
    product = await product_exists(session, product_id)
    # Consistent lock order for multi-warehouse requests.
    warehouses = {}
    for wid in sorted(set(warehouse_ids)):
        warehouses[wid] = await warehouse_locked(session, wid, active=active)
        if warehouses[wid].store_id != product.store_id:
            failure("Товар и склад принадлежат разным магазинам")
    return product, warehouses


async def locked_balance(session: AsyncSession, product_id: int, warehouse_id: int) -> StockBalance:
    # Insert-on-conflict avoids the missing-row race for new products/warehouses.
    await session.execute(
        pg_insert(StockBalance)
        .values(product_id=product_id, warehouse_id=warehouse_id, on_hand=0, reserved=0)
        .on_conflict_do_nothing(constraint="uq_balance_warehouse_product")
    )
    balance = await session.scalar(
        select(StockBalance)
        .where(StockBalance.product_id == product_id, StockBalance.warehouse_id == warehouse_id)
        .with_for_update()
    )
    assert balance is not None
    return balance


def ensure_available(balance: StockBalance, quantity: int, warehouse: Warehouse, session_note: str = "") -> None:
    available = balance.on_hand - balance.reserved
    if available < quantity:
        failure(
            f"На ФФ «{warehouse.name}» доступно {available} шт., требуется {quantity}. "
            "Проверьте другой ФФ или выполните перемещение."
        )


def event(session: AsyncSession, *, product_id: int, kind: str, quantity: int,
          source: int | None = None, destination: int | None = None,
          ref_type: str | None = None, ref_id: int | None = None,
          note: str | None = None, key: str | None = None) -> None:
    session.add(StockMovement(
        product_id=product_id, kind=kind, quantity=quantity,
        source_warehouse_id=source, destination_warehouse_id=destination,
        reference_type=ref_type, reference_id=ref_id, note=note, request_key=key,
    ))


async def receipt(session: AsyncSession, payload: ReceiptIn) -> dict:
    async with session.begin():
        await lock_idempotency_key(session, "receipt", payload.request_key)
        existing = await session.scalar(select(StockMovement).where(StockMovement.request_key == payload.request_key))
        if existing:
            assert_same(existing, product_id=payload.product_id,
                        destination_warehouse_id=payload.warehouse_id,
                        quantity=payload.quantity, kind="receipt")
            return {"id": existing.id, "status": "already_applied"}

        await pair_check(session, payload.product_id, payload.warehouse_id)
        balance = await locked_balance(session, payload.product_id, payload.warehouse_id)
        balance.on_hand += payload.quantity
        movement = StockMovement(
            product_id=payload.product_id, destination_warehouse_id=payload.warehouse_id,
            kind="receipt", quantity=payload.quantity, request_key=payload.request_key,
        )
        session.add(movement)
        await session.flush()
        return {"id": movement.id, "status": "applied", "on_hand": balance.on_hand, "available": balance.available}


async def create_transfer(session: AsyncSession, payload: TransferIn) -> dict:
    if payload.source_warehouse_id == payload.destination_warehouse_id:
        failure("Источник и получатель перемещения должны отличаться", 422)
    async with session.begin():
        await lock_idempotency_key(session, "transfer", payload.request_key)
        existing = await session.scalar(select(Transfer).where(Transfer.request_key == payload.request_key))
        if existing:
            assert_same(existing, product_id=payload.product_id, source_warehouse_id=payload.source_warehouse_id,
                        destination_warehouse_id=payload.destination_warehouse_id, quantity=payload.quantity)
            return {"id": existing.id, "status": existing.status, "replayed": True}

        _, wh = await pair_check(session, payload.product_id, payload.source_warehouse_id, payload.destination_warehouse_id)
        balance = await locked_balance(session, payload.product_id, payload.source_warehouse_id)
        ensure_available(balance, payload.quantity, wh[payload.source_warehouse_id])
        balance.on_hand -= payload.quantity
        transfer = Transfer(product_id=payload.product_id, source_warehouse_id=payload.source_warehouse_id,
                            destination_warehouse_id=payload.destination_warehouse_id,
                            quantity=payload.quantity, request_key=payload.request_key)
        session.add(transfer)
        await session.flush()
        event(session, product_id=payload.product_id, kind="transfer_sent", quantity=payload.quantity,
              source=payload.source_warehouse_id, destination=payload.destination_warehouse_id,
              ref_type="transfer", ref_id=transfer.id)
        return {"id": transfer.id, "status": transfer.status, "replayed": False}


async def receive_transfer(session: AsyncSession, transfer_id: int) -> dict:
    async with session.begin():
        transfer = await session.scalar(select(Transfer).where(Transfer.id == transfer_id).with_for_update())
        if transfer is None:
            failure("Перемещение не найдено", 404)
        if transfer.status == "received":
            return {"id": transfer.id, "status": "received", "replayed": True}
        if transfer.status == "cancelled":
            failure("Отменённое перемещение нельзя принять")
        # Receipt is allowed on an archived destination: shipment existed before archival.
        await pair_check(session, transfer.product_id, transfer.destination_warehouse_id, active=False)
        balance = await locked_balance(session, transfer.product_id, transfer.destination_warehouse_id)
        balance.on_hand += transfer.quantity
        transfer.status = "received"
        transfer.received_at = datetime.now(timezone.utc)
        event(session, product_id=transfer.product_id, kind="transfer_received", quantity=transfer.quantity,
              source=transfer.source_warehouse_id, destination=transfer.destination_warehouse_id,
              ref_type="transfer", ref_id=transfer.id)
        return {"id": transfer.id, "status": transfer.status, "replayed": False}


async def cancel_transfer(session: AsyncSession, transfer_id: int) -> dict:
    """Undo an unreceived dispatch once, atomically restoring source FF stock.

    Only valid when the goods were not actually dispatched, or were returned
    physically to the source FF. This is not a receipt at the destination.
    """
    async with session.begin():
        transfer = await session.scalar(select(Transfer).where(Transfer.id == transfer_id).with_for_update())
        if transfer is None:
            failure("Перемещение не найдено", 404)
        if transfer.status == "cancelled":
            return {"id": transfer.id, "status": "cancelled", "replayed": True}
        if transfer.status != "in_transit":
            failure("Уже принятое перемещение нельзя отменить. Оформите обратное перемещение")

        # Return to the original FF, even when it was archived after dispatch.
        await pair_check(session, transfer.product_id, transfer.source_warehouse_id, active=False)
        source_balance = await locked_balance(session, transfer.product_id, transfer.source_warehouse_id)
        source_balance.on_hand += transfer.quantity
        transfer.status = "cancelled"
        transfer.cancelled_at = datetime.now(timezone.utc)
        event(session, product_id=transfer.product_id, kind="transfer_cancelled",
              quantity=transfer.quantity, source=None,
              destination=transfer.source_warehouse_id,
              ref_type="transfer", ref_id=transfer.id,
              note="Отмена отправки: товар возвращён в остаток исходного ФФ")
        return {"id": transfer.id, "status": "cancelled", "replayed": False}


async def reserve_fbs(session: AsyncSession, payload: ReserveIn) -> dict:
    async with session.begin():
        await lock_idempotency_key(session, "fbs-reserve", payload.request_key)
        existing = await session.scalar(select(FBSReservation).where(FBSReservation.request_key == payload.request_key))
        if existing:
            assert_same(existing, product_id=payload.product_id, warehouse_id=payload.warehouse_id,
                        quantity=payload.quantity, order_number=payload.order_number)
            return {"id": existing.id, "status": existing.status, "replayed": True}

        _, wh = await pair_check(session, payload.product_id, payload.warehouse_id)
        balance = await locked_balance(session, payload.product_id, payload.warehouse_id)
        ensure_available(balance, payload.quantity, wh[payload.warehouse_id])
        balance.reserved += payload.quantity
        reservation = FBSReservation(product_id=payload.product_id, warehouse_id=payload.warehouse_id,
                                     quantity=payload.quantity, order_number=payload.order_number,
                                     request_key=payload.request_key)
        session.add(reservation)
        await session.flush()
        event(session, product_id=payload.product_id, kind="fbs_reserved", quantity=payload.quantity,
              source=payload.warehouse_id, ref_type="fbs_reservation", ref_id=reservation.id)
        return {"id": reservation.id, "status": reservation.status, "replayed": False}


async def cancel_fbs(session: AsyncSession, reservation_id: int) -> dict:
    async with session.begin():
        reservation = await session.scalar(select(FBSReservation).where(FBSReservation.id == reservation_id).with_for_update())
        if reservation is None:
            failure("Резерв FBS не найден", 404)
        if reservation.status == "cancelled":
            return {"id": reservation.id, "status": reservation.status, "replayed": True}
        if reservation.status != "active":
            failure("Уже отправленный FBS-заказ отменить как резерв нельзя")
        await pair_check(session, reservation.product_id, reservation.warehouse_id, active=False)
        balance = await locked_balance(session, reservation.product_id, reservation.warehouse_id)
        balance.reserved -= reservation.quantity
        reservation.status = "cancelled"
        reservation.completed_at = datetime.now(timezone.utc)
        event(session, product_id=reservation.product_id, kind="fbs_cancelled", quantity=reservation.quantity,
              source=reservation.warehouse_id, ref_type="fbs_reservation", ref_id=reservation.id)
        return {"id": reservation.id, "status": reservation.status, "replayed": False}


async def ship_fbs(session: AsyncSession, reservation_id: int) -> dict:
    async with session.begin():
        reservation = await session.scalar(select(FBSReservation).where(FBSReservation.id == reservation_id).with_for_update())
        if reservation is None:
            failure("Резерв FBS не найден", 404)
        if reservation.status == "shipped":
            shipment = await session.scalar(select(Shipment).where(Shipment.fbs_reservation_id == reservation_id))
            if shipment is None:
                failure("Отправка по резерву отсутствует: нарушение целостности")
            return {"id": shipment.id, "reservation_id": reservation.id, "status": "in_transit", "replayed": True}
        if reservation.status != "active":
            failure("Отменённый резерв FBS нельзя отправить")
        await pair_check(session, reservation.product_id, reservation.warehouse_id, active=False)
        balance = await locked_balance(session, reservation.product_id, reservation.warehouse_id)
        balance.reserved -= reservation.quantity
        balance.on_hand -= reservation.quantity
        reservation.status = "shipped"
        reservation.completed_at = datetime.now(timezone.utc)
        shipment = Shipment(shipment_type="FBS", product_id=reservation.product_id,
                            source_warehouse_id=reservation.warehouse_id,
                            fbs_reservation_id=reservation.id,
                            quantity=reservation.quantity, destination="Клиент")
        session.add(shipment)
        await session.flush()
        event(session, product_id=reservation.product_id, kind="fbs_shipped", quantity=reservation.quantity,
              source=reservation.warehouse_id, ref_type="shipment", ref_id=shipment.id)
        return {"id": shipment.id, "reservation_id": reservation.id, "status": shipment.status, "replayed": False}


async def ship_fbo(session: AsyncSession, payload: FBOIn) -> dict:
    async with session.begin():
        await lock_idempotency_key(session, "fbo-ship", payload.request_key)
        existing = await session.scalar(select(Shipment).where(Shipment.request_key == payload.request_key))
        if existing:
            assert_same(existing, shipment_type="FBO", product_id=payload.product_id,
                        source_warehouse_id=payload.warehouse_id, quantity=payload.quantity,
                        destination=payload.destination)
            return {"id": existing.id, "status": existing.status, "replayed": True}

        _, wh = await pair_check(session, payload.product_id, payload.warehouse_id)
        balance = await locked_balance(session, payload.product_id, payload.warehouse_id)
        ensure_available(balance, payload.quantity, wh[payload.warehouse_id])
        balance.on_hand -= payload.quantity
        shipment = Shipment(shipment_type="FBO", product_id=payload.product_id,
                            source_warehouse_id=payload.warehouse_id, quantity=payload.quantity,
                            destination=payload.destination, request_key=payload.request_key)
        session.add(shipment)
        await session.flush()
        event(session, product_id=payload.product_id, kind="fbo_shipped", quantity=payload.quantity,
              source=payload.warehouse_id, ref_type="shipment", ref_id=shipment.id,
              note=f"На маркетплейс: {payload.destination}")
        return {"id": shipment.id, "status": shipment.status, "replayed": False}


async def receive_fbo(session: AsyncSession, shipment_id: int) -> dict:
    async with session.begin():
        shipment = await session.scalar(select(Shipment).where(Shipment.id == shipment_id).with_for_update())
        if shipment is None:
            failure("Отправка не найдена", 404)
        if shipment.shipment_type != "FBO":
            failure("Приёмка на маркетплейсе применима только к FBO")
        if shipment.status == "received":
            return {"id": shipment.id, "status": shipment.status, "replayed": True}
        shipment.status = "received"
        shipment.received_at = datetime.now(timezone.utc)
        # Marketplace stock is not FF stock: keep as shipment with 'received'.
        event(session, product_id=shipment.product_id, kind="fbo_received", quantity=shipment.quantity,
              source=shipment.source_warehouse_id, ref_type="shipment", ref_id=shipment.id,
              note=f"Принято маркетплейсом: {shipment.destination}")
        return {"id": shipment.id, "status": shipment.status, "replayed": False}


async def demo_store_id(session: AsyncSession) -> int:
    store_id = await session.scalar(select(Store.id).where(Store.code == "demo"))
    if store_id is None:
        failure("Демонстрационный магазин не создан. Проверьте seed.", 503)
    return store_id


async def add_product(session: AsyncSession, sku: str, name: str) -> dict:
    async with session.begin():
        store_id = await demo_store_id(session)
        sku, name = sku.strip(), name.strip()
        if not sku or not name:
            failure("Артикул и название не могут быть пустыми", 422)
        if await session.scalar(select(Product.id).where(Product.store_id == store_id, Product.sku == sku)):
            failure("Артикул уже существует")
        obj = Product(store_id=store_id, sku=sku, name=name)
        session.add(obj)
        await session.flush()
        return {"id": obj.id, "sku": obj.sku, "name": obj.name}


async def add_warehouse(session: AsyncSession, code: str, name: str) -> dict:
    async with session.begin():
        store_id = await demo_store_id(session)
        code, name = code.strip(), name.strip()
        if not code or not name:
            failure("Код и название склада не могут быть пустыми", 422)
        if await session.scalar(select(Warehouse.id).where(Warehouse.store_id == store_id, Warehouse.code == code)):
            failure("Такой код склада уже существует")
        obj = Warehouse(store_id=store_id, code=code, name=name)
        session.add(obj)
        await session.flush()
        return {"id": obj.id, "code": obj.code, "name": obj.name, "active": True}


async def rename_warehouse(session: AsyncSession, warehouse_id: int, name: str) -> dict:
    async with session.begin():
        obj = await warehouse_locked(session, warehouse_id, active=False)
        obj.name = name.strip()
        if not obj.name:
            failure("Название не может быть пустым", 422)
        return {"id": obj.id, "name": obj.name, "active": obj.is_active}


async def archive_warehouse(session: AsyncSession, warehouse_id: int) -> dict:
    async with session.begin():
        obj = await warehouse_locked(session, warehouse_id, active=False)
        if obj.is_active:
            obj.is_active = False
            obj.archived_at = datetime.now(timezone.utc)
        # No physical deletion; old balances, reservations, references are retained.
        return {"id": obj.id, "name": obj.name, "active": False}
