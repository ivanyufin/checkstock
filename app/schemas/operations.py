"""Input contracts for CheckStock write operations."""
from pydantic import BaseModel, Field


class RequestKey(BaseModel):
    # Client-generated UUID is recommended. Retrying the same request with the
    # same key is safe, even after a lost HTTP response.
    request_key: str = Field(min_length=8, max_length=100)


class ReceiptIn(RequestKey):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1_000_000)


class TransferIn(RequestKey):
    product_id: int = Field(gt=0)
    source_warehouse_id: int = Field(gt=0)
    destination_warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1_000_000)


class ReserveIn(RequestKey):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1_000_000)
    order_number: str | None = Field(default=None, max_length=100)


class FBOIn(RequestKey):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1_000_000)
    destination: str = Field(min_length=1, max_length=180)


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=220)


class WarehouseIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=180)


class WarehousePatch(BaseModel):
    name: str = Field(min_length=1, max_length=180)
