import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.widget import WidgetConfig

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Schemas ---

class WidgetCreate(BaseModel):
    widget_type: str
    grid_col: int = 0
    grid_row: int = 0
    grid_width: int = 1
    grid_height: int = 1
    config: dict = {}


class WidgetUpdate(BaseModel):
    widget_type: str | None = None
    grid_col: int | None = None
    grid_row: int | None = None
    grid_width: int | None = None
    grid_height: int | None = None
    config: dict | None = None
    is_visible: bool | None = None


class WidgetResponse(BaseModel):
    id: str
    widget_type: str
    grid_col: int
    grid_row: int
    grid_width: int
    grid_height: int
    config: dict
    is_visible: bool


class DashboardLayout(BaseModel):
    dashboard_id: str
    widgets: list[WidgetResponse]


class LayoutBulkUpdate(BaseModel):
    widgets: list[dict]  # [{id, grid_col, grid_row, grid_width, grid_height}]


def _to_response(w: WidgetConfig) -> WidgetResponse:
    return WidgetResponse(
        id=w.id,
        widget_type=w.widget_type,
        grid_col=w.grid_col,
        grid_row=w.grid_row,
        grid_width=w.grid_width,
        grid_height=w.grid_height,
        config=json.loads(w.config) if w.config else {},
        is_visible=bool(w.is_visible),
    )


# --- Routes ---

@router.get("/{dashboard_id}")
async def get_dashboard(
    dashboard_id: str, db: AsyncSession = Depends(get_db)
) -> DashboardLayout:
    result = await db.execute(
        select(WidgetConfig)
        .where(WidgetConfig.dashboard_id == dashboard_id)
        .order_by(WidgetConfig.grid_row, WidgetConfig.grid_col)
    )
    widgets = result.scalars().all()
    return DashboardLayout(
        dashboard_id=dashboard_id,
        widgets=[_to_response(w) for w in widgets],
    )


@router.put("/{dashboard_id}")
async def update_layout(
    dashboard_id: str, data: LayoutBulkUpdate, db: AsyncSession = Depends(get_db)
) -> DashboardLayout:
    for item in data.widgets:
        wid = item.get("id")
        if not wid:
            continue
        result = await db.execute(
            select(WidgetConfig).where(
                WidgetConfig.id == wid, WidgetConfig.dashboard_id == dashboard_id
            )
        )
        widget = result.scalar_one_or_none()
        if widget:
            if "grid_col" in item:
                widget.grid_col = item["grid_col"]
            if "grid_row" in item:
                widget.grid_row = item["grid_row"]
            if "grid_width" in item:
                widget.grid_width = item["grid_width"]
            if "grid_height" in item:
                widget.grid_height = item["grid_height"]
            widget.updated_at = _now()

    await db.commit()
    return await get_dashboard(dashboard_id, db)


@router.post("/{dashboard_id}/widgets", status_code=201)
async def create_widget(
    dashboard_id: str, data: WidgetCreate, db: AsyncSession = Depends(get_db)
) -> WidgetResponse:
    widget = WidgetConfig(
        id=_gen_id(),
        dashboard_id=dashboard_id,
        widget_type=data.widget_type,
        grid_col=data.grid_col,
        grid_row=data.grid_row,
        grid_width=data.grid_width,
        grid_height=data.grid_height,
        config=json.dumps(data.config),
    )
    db.add(widget)
    await db.commit()
    await db.refresh(widget)
    return _to_response(widget)


@router.put("/widgets/{widget_id}")
async def update_widget(
    widget_id: str, data: WidgetUpdate, db: AsyncSession = Depends(get_db)
) -> WidgetResponse:
    result = await db.execute(select(WidgetConfig).where(WidgetConfig.id == widget_id))
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(404, "Widget nicht gefunden")

    if data.widget_type is not None:
        widget.widget_type = data.widget_type
    if data.grid_col is not None:
        widget.grid_col = data.grid_col
    if data.grid_row is not None:
        widget.grid_row = data.grid_row
    if data.grid_width is not None:
        widget.grid_width = data.grid_width
    if data.grid_height is not None:
        widget.grid_height = data.grid_height
    if data.config is not None:
        widget.config = json.dumps(data.config)
    if data.is_visible is not None:
        widget.is_visible = 1 if data.is_visible else 0
    widget.updated_at = _now()

    await db.commit()
    await db.refresh(widget)
    return _to_response(widget)


@router.delete("/widgets/{widget_id}")
async def delete_widget(widget_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WidgetConfig).where(WidgetConfig.id == widget_id))
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(404, "Widget nicht gefunden")
    await db.delete(widget)
    await db.commit()
    return {"success": True}
