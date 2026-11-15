"""PostgreSQL integration tests — isolated DB via docker compose --profile test."""
import asyncio
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import engine
from app.main import app
from app.models import Base, StockMovement
from app.seed import seed_demo
from app.database import AsyncSessionLocal

pytestmark = pytest.mark.asyncio


def key() -> str:
    return str(uuid4())


@pytest_asyncio.fixture
async def client():
    if os.getenv("CHECKSTOCK_TESTING") != "1" or "test-db" not in os.getenv("DATABASE_URL", ""):
        pytest.skip("Run integration tests via docker compose --profile test run --rm tests")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await seed_demo()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        yield api
    await engine.dispose()


async def balance(client, product="SKU-101", warehouse="Север"):
    response = await client.get("/api/inventory")
    assert response.status_code == 200
    item = next(x for x in response.json()["items"] if x["sku"] == product)
    return next(x for x in item["warehouses"] if x["warehouse_name"] == warehouse)


async def test_receipt_idempotent_and_request_key_collision(client):
    body = {"product_id": 1, "warehouse_id": 1, "quantity": 3, "request_key": key()}
    result = await client.post("/api/inventory/receipt", json=body)
    assert result.status_code == 201, result.text
    assert (await balance(client))["on_hand"] == 13

    repeated = await client.post("/api/inventory/receipt", json=body)
    assert repeated.status_code == 201
    assert repeated.json()["status"] == "already_applied"
    assert (await balance(client))["on_hand"] == 13

    conflicting = await client.post("/api/inventory/receipt", json={**body, "quantity": 99})
    assert conflicting.status_code == 409
    assert (await balance(client))["on_hand"] == 13


async def test_fbs_reserve_cancel_and_insufficient_stock(client):
    reserve = await client.post("/api/fbs/reserve", json={
        "product_id": 1, "warehouse_id": 1, "quantity": 8,
        "order_number": "ORDER-A", "request_key": key(),
    })
    assert reserve.status_code == 201, reserve.text
    reservation_id = reserve.json()["id"]
    assert (await balance(client))["reserved"] == 8
    assert (await balance(client))["available"] == 2

    fail = await client.post("/api/fbs/reserve", json={
        "product_id": 1, "warehouse_id": 1, "quantity": 5,
        "request_key": key(),
    })
    assert fail.status_code == 409
    assert "Юг" not in fail.json()["detail"]  # refuses the chosen FF, even if others have stock
    assert (await balance(client))["available"] == 2

    cancel = await client.post(f"/api/fbs/{reservation_id}/cancel")
    assert cancel.status_code == 200
    assert (await balance(client))["available"] == 10
    assert (await client.post(f"/api/fbs/{reservation_id}/cancel")).json()["replayed"] is True
    assert (await balance(client))["available"] == 10
    assert (await client.post(f"/api/fbs/{reservation_id}/ship")).status_code == 409


async def test_fbs_ship_exactly_once(client):
    order = await client.post("/api/fbs/reserve", json={
        "product_id": 1, "warehouse_id": 1, "quantity": 4, "request_key": key(),
    })
    assert order.status_code == 201
    rid = order.json()["id"]
    ship = await client.post(f"/api/fbs/{rid}/ship")
    assert ship.status_code == 201
    replayed = await client.post(f"/api/fbs/{rid}/ship")
    assert replayed.status_code == 201
    assert replayed.json()["replayed"] is True
    assert ship.json()["id"] == replayed.json()["id"]
    stock = await balance(client)
    assert (stock["on_hand"], stock["reserved"], stock["available"]) == (6, 0, 6)
    overview = (await client.get("/api/inventory")).json()["totals"]
    assert overview["transit_to_customer_fbs"] == 4


async def test_transfer_in_transit_and_receive_once(client):
    create = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 2, "quantity": 3, "request_key": key(),
    })
    assert create.status_code == 201, create.text
    transfer_id = create.json()["id"]
    assert (await balance(client, warehouse="Север"))["on_hand"] == 7
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 6
    assert (await client.get("/api/inventory")).json()["totals"]["transit_between_warehouses"] == 3

    receive = await client.post(f"/api/transfers/{transfer_id}/receive")
    replay = await client.post(f"/api/transfers/{transfer_id}/receive")
    assert receive.status_code == 200
    assert replay.json()["replayed"] is True
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 9
    assert (await client.get("/api/inventory")).json()["totals"]["transit_between_warehouses"] == 0


async def test_fbo_ship_receive_and_no_double_acceptance(client):
    create = await client.post("/api/fbo/ship", json={
        "product_id": 1, "warehouse_id": 2, "quantity": 2,
        "destination": "Маркетплейс A", "request_key": key(),
    })
    assert create.status_code == 201, create.text
    shipment_id = create.json()["id"]
    totals = (await client.get("/api/inventory")).json()["totals"]
    assert totals["transit_to_marketplace_fbo"] == 2
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 4

    received = await client.post(f"/api/fbo/{shipment_id}/receive")
    repeated = await client.post(f"/api/fbo/{shipment_id}/receive")
    assert received.status_code == 200
    assert repeated.json()["replayed"] is True
    totals = (await client.get("/api/inventory")).json()["totals"]
    assert totals["transit_to_marketplace_fbo"] == 0
    assert totals["received_on_marketplace_fbo"] == 2
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 4


async def test_archive_with_stock_retains_history_and_blocks_new_ops(client):
    delete = await client.delete("/api/warehouses/1")
    assert delete.status_code == 200
    assert delete.json()["active"] is False
    assert (await balance(client))["on_hand"] == 10
    denied = await client.post("/api/inventory/receipt", json={
        "product_id": 1, "warehouse_id": 1, "quantity": 1, "request_key": key(),
    })
    assert denied.status_code == 409
    assert (await balance(client))["on_hand"] == 10
    assert (await client.delete("/api/warehouses/1")).status_code == 200
    assert (await client.get("/api/movements")).json()["items"]


async def test_archive_destination_can_receive_existing_transfer(client):
    sent = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 2, "quantity": 2, "request_key": key(),
    })
    tid = sent.json()["id"]
    assert (await client.delete("/api/warehouses/2")).status_code == 200
    assert (await client.post(f"/api/transfers/{tid}/receive")).status_code == 200
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 8


async def test_invalid_quantity_and_warehouse_scope(client):
    bad = await client.post("/api/fbo/ship", json={
        "product_id": 1, "warehouse_id": 1, "quantity": -5,
        "destination": "MP", "request_key": key(),
    })
    assert bad.status_code == 422
    same = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 1, "quantity": 2, "request_key": key(),
    })
    assert same.status_code == 422


async def test_concurrent_reservation_cannot_oversell(client):
    async def attempt(n):
        return await client.post("/api/fbs/reserve", json={
            "product_id": 2, "warehouse_id": 1, "quantity": 4,
            "order_number": f"concurrent-{n}", "request_key": key(),
        })
    a, b = await asyncio.gather(attempt(1), attempt(2))
    assert sorted([a.status_code, b.status_code]) == [201, 409]
    assert (await balance(client, product="SKU-102"))["reserved"] == 4


async def test_concurrent_double_confirm_does_not_double_ship(client):
    reserve = await client.post("/api/fbs/reserve", json={
        "product_id": 1, "warehouse_id": 1, "quantity": 3, "request_key": key(),
    })
    rid = reserve.json()["id"]
    x, y = await asyncio.gather(
        client.post(f"/api/fbs/{rid}/ship"),
        client.post(f"/api/fbs/{rid}/ship"),
    )
    assert x.status_code == y.status_code == 201
    assert x.json()["id"] == y.json()["id"]
    assert (await balance(client))["on_hand"] == 7
    async with AsyncSessionLocal() as session:
        count = (await session.scalars(select(StockMovement).where(
            StockMovement.kind == "fbs_shipped", StockMovement.reference_id == x.json()["id"]
        ))).all()
    assert len(count) == 1

async def test_manager_dashboard_and_static_assets(client):
    """The manager must be able to open the interface without Swagger."""
    page = await client.get("/")
    assert page.status_code == 200, page.text
    assert "Товары и остатки" in page.text
    assert "Перемещения ФФ" in page.text
    assert "operation-form" in page.text
    js = await client.get("/static/js/app.js")
    assert js.status_code == 200
    assert "submitOperation" in js.text
    css = await client.get("/static/css/app.css")
    assert css.status_code == 200


async def test_manager_list_endpoints(client):
    """All UI read sources respond with the expected collections."""
    for path in ("/api/inventory", "/api/warehouses", "/api/transfers", "/api/fbs", "/api/shipments", "/api/movements"):
        result = await client.get(path)
        assert result.status_code == 200, (path, result.text)
        assert isinstance(result.json()["items"], list)


async def test_transfer_cancel_restores_source_and_keeps_history(client):
    sent = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 2, "quantity": 4, "request_key": key(),
    })
    assert sent.status_code == 201, sent.text
    tid = sent.json()["id"]
    assert (await balance(client, warehouse="Север"))["on_hand"] == 6
    assert (await client.get("/api/inventory")).json()["totals"]["transit_between_warehouses"] == 4

    cancelled = await client.post(f"/api/transfers/{tid}/cancel")
    repeated = await client.post(f"/api/transfers/{tid}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["replayed"] is False
    assert repeated.json()["replayed"] is True
    assert (await balance(client, warehouse="Север"))["on_hand"] == 10
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 6
    assert (await client.get("/api/inventory")).json()["totals"]["transit_between_warehouses"] == 0
    rows = (await client.get("/api/transfers")).json()["items"]
    assert next(t for t in rows if t["id"] == tid)["status"] == "cancelled"
    history = (await client.get("/api/movements")).json()["items"]
    events = [m for m in history if m["reference_type"] == "transfer" and m["reference_id"] == tid]
    assert sorted(m["kind"] for m in events) == ["transfer_cancelled", "transfer_sent"]
    assert (await client.post(f"/api/transfers/{tid}/receive")).status_code == 409


async def test_received_transfer_cannot_be_cancelled(client):
    sent = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 2, "quantity": 3, "request_key": key(),
    })
    tid = sent.json()["id"]
    assert (await client.post(f"/api/transfers/{tid}/receive")).status_code == 200
    denied = await client.post(f"/api/transfers/{tid}/cancel")
    assert denied.status_code == 409
    assert (await balance(client, warehouse="Север"))["on_hand"] == 7
    assert (await balance(client, warehouse="Юг"))["on_hand"] == 9


async def test_transfer_can_be_cancelled_after_source_archived(client):
    sent = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 2, "quantity": 2, "request_key": key(),
    })
    tid = sent.json()["id"]
    assert (await client.delete("/api/warehouses/1")).status_code == 200
    assert (await client.post(f"/api/transfers/{tid}/cancel")).status_code == 200
    assert (await balance(client, warehouse="Север"))["on_hand"] == 10
    assert (await client.get("/api/inventory")).json()["totals"]["transit_between_warehouses"] == 0


async def test_concurrent_cancel_and_receive_only_one_wins(client):
    sent = await client.post("/api/transfers", json={
        "product_id": 1, "source_warehouse_id": 1,
        "destination_warehouse_id": 2, "quantity": 2, "request_key": key(),
    })
    tid = sent.json()["id"]
    cancel, receive = await asyncio.gather(
        client.post(f"/api/transfers/{tid}/cancel"),
        client.post(f"/api/transfers/{tid}/receive"),
    )
    assert sorted([cancel.status_code, receive.status_code]) == [200, 409]
    src = (await balance(client, warehouse="Север"))["on_hand"]
    dst = (await balance(client, warehouse="Юг"))["on_hand"]
    assert (src, dst) in [(10, 6), (8, 8)]
    assert (await client.get("/api/inventory")).json()["totals"]["transit_between_warehouses"] == 0
