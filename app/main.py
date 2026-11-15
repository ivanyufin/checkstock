"""CheckStock application entry point."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from app.routers.operations import router as operations_router
from app.services.operations import InventoryError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import engine, get_session
from app.inventory_read import inventory_snapshot, recent_movements

BASE = Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()

app = FastAPI(title=settings.app_name, version="0.4.0", lifespan=lifespan)
app.include_router(operations_router)


@app.exception_handler(InventoryError)
async def inventory_error_handler(request: Request, exc: InventoryError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(IntegrityError)
async def database_integrity_handler(request: Request, exc: IntegrityError):
    # Do not expose SQL, passwords, or internal constraint details to users.
    return JSONResponse(status_code=409, content={"detail": "Конфликт данных или ограничение базы данных"})
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/db", tags=["system"])
async def health_db(session: AsyncSession = Depends(get_session)):
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.get("/api/inventory", tags=["inventory"])
async def get_inventory(session: AsyncSession = Depends(get_session)):
    return await inventory_snapshot(session)


@app.get("/api/movements", tags=["inventory"])
async def get_movements(session: AsyncSession = Depends(get_session)):
    return {"items": await recent_movements(session, limit=100)}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    snapshot = await inventory_snapshot(session)
    movements = await recent_movements(session)
    return templates.TemplateResponse(request, "dashboard.html", {"snapshot": snapshot, "movements": movements})
