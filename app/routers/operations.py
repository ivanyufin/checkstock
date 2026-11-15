"""API routes delegate state changes to atomic services."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import FBSReservation, Shipment, Transfer, Warehouse
from app.schemas.operations import (
    FBOIn, ProductIn, ReceiptIn, ReserveIn, TransferIn, WarehouseIn, WarehousePatch,
)
from app.services import operations as ops

router = APIRouter(prefix="/api", tags=["operations"])


@router.post("/inventory/receipt", status_code=201)
async def receipt(payload: ReceiptIn, session: AsyncSession = Depends(get_session)):
    return await ops.receipt(session, payload)


@router.post("/transfers", status_code=201)
async def transfer(payload: TransferIn, session: AsyncSession = Depends(get_session)):
    return await ops.create_transfer(session, payload)


@router.post("/transfers/{transfer_id}/receive")
async def transfer_receive(transfer_id: int, session: AsyncSession = Depends(get_session)):
    return await ops.receive_transfer(session, transfer_id)


@router.post("/transfers/{transfer_id}/cancel")
async def transfer_cancel(transfer_id: int, session: AsyncSession = Depends(get_session)):
    return await ops.cancel_transfer(session, transfer_id)


@router.post("/fbs/reserve", status_code=201)
async def reserve(payload: ReserveIn, session: AsyncSession = Depends(get_session)):
    return await ops.reserve_fbs(session, payload)


@router.post("/fbs/{reservation_id}/cancel")
async def cancel(reservation_id: int, session: AsyncSession = Depends(get_session)):
    return await ops.cancel_fbs(session, reservation_id)


@router.post("/fbs/{reservation_id}/ship", status_code=201)
async def fbs_ship(reservation_id: int, session: AsyncSession = Depends(get_session)):
    return await ops.ship_fbs(session, reservation_id)


@router.post("/fbo/ship", status_code=201)
async def fbo_ship(payload: FBOIn, session: AsyncSession = Depends(get_session)):
    return await ops.ship_fbo(session, payload)


@router.post("/fbo/{shipment_id}/receive")
async def fbo_receive(shipment_id: int, session: AsyncSession = Depends(get_session)):
    return await ops.receive_fbo(session, shipment_id)


@router.post("/products", status_code=201)
async def add_product(payload: ProductIn, session: AsyncSession = Depends(get_session)):
    return await ops.add_product(session, payload.sku, payload.name)


@router.post("/warehouses", status_code=201)
async def add_warehouse(payload: WarehouseIn, session: AsyncSession = Depends(get_session)):
    return await ops.add_warehouse(session, payload.code, payload.name)


@router.patch("/warehouses/{warehouse_id}")
async def patch_warehouse(warehouse_id: int, payload: WarehousePatch, session: AsyncSession = Depends(get_session)):
    return await ops.rename_warehouse(session, warehouse_id, payload.name)


@router.delete("/warehouses/{warehouse_id}")
async def delete_warehouse(warehouse_id: int, session: AsyncSession = Depends(get_session)):
    return await ops.archive_warehouse(session, warehouse_id)


@router.get("/warehouses")
async def warehouses(session: AsyncSession = Depends(get_session)):
    result = (await session.scalars(select(Warehouse).order_by(Warehouse.id))).all()
    return {"items": [{"id": w.id, "code": w.code, "name": w.name, "active": w.is_active} for w in result]}


@router.get("/transfers")
async def transfers(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Transfer).order_by(Transfer.id.desc()))).all()
    return {"items": [
        {"id": t.id, "product_id": t.product_id, "source_warehouse_id": t.source_warehouse_id,
         "destination_warehouse_id": t.destination_warehouse_id, "quantity": t.quantity,
         "status": t.status} for t in rows
    ]}


@router.get("/fbs")
async def fbs(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(FBSReservation).order_by(FBSReservation.id.desc()))).all()
    return {"items": [
        {"id": r.id, "product_id": r.product_id, "warehouse_id": r.warehouse_id,
         "quantity": r.quantity, "status": r.status, "order_number": r.order_number} for r in rows
    ]}


@router.get("/shipments")
async def shipments(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Shipment).order_by(Shipment.id.desc()))).all()
    return {"items": [
        {"id": s.id, "type": s.shipment_type, "product_id": s.product_id,
         "source_warehouse_id": s.source_warehouse_id, "quantity": s.quantity,
         "status": s.status, "destination": s.destination} for s in rows
    ]}
