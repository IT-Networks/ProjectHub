import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.communication import LinkedMessage
from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Schemas ---

class LinkCreate(BaseModel):
    link_target: str  # project, todo, note
    target_id: str
    source: str  # email, webex
    source_ref: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    snippet: str = ""
    snapshot: dict = {}


class LinkedMessageResponse(BaseModel):
    id: str
    link_target: str
    target_id: str
    source: str
    source_ref: str
    subject: str
    sender: str
    date: str
    snippet: str
    created_at: str


def _to_response(m: LinkedMessage) -> LinkedMessageResponse:
    return LinkedMessageResponse(
        id=m.id,
        link_target=m.link_target,
        target_id=m.target_id,
        source=m.source,
        source_ref=m.source_ref,
        subject=m.subject,
        sender=m.sender,
        date=m.date,
        snippet=m.snippet,
        created_at=m.created_at,
    )


# --- Email Proxy ---

@router.get("/emails")
async def search_emails(
    query: str = Query(""),
    sender: str = Query(""),
    subject: str = Query(""),
    folder: str = Query("inbox"),
    limit: int = Query(20),
):
    """Proxy to AI-Assist email search."""
    data = await ai_assist.post("/api/email/search", {
        "query": query,
        "sender": sender,
        "subject": subject,
        "folder": folder,
        "limit": limit,
    })
    if data is None:
        return {"success": False, "results": [], "total": 0, "error": "AI-Assist nicht erreichbar"}
    return data


@router.get("/emails/{email_id}")
async def read_email(email_id: str, folder: str = Query("inbox")):
    """Proxy to AI-Assist email read."""
    data = await ai_assist.get(f"/api/email/read/{email_id}", params={"folder": folder})
    if data is None:
        raise HTTPException(503, "AI-Assist nicht erreichbar")
    return data


# --- Webex Proxy ---

@router.get("/webex/rooms")
async def list_webex_rooms():
    """Proxy to AI-Assist Webex rooms."""
    data = await ai_assist.get_webex_rooms()
    if data is None:
        return {"rooms": [], "count": 0}
    return data


@router.get("/webex/rooms/{room_id}/messages")
async def list_webex_messages(room_id: str, limit: int = Query(50)):
    """Proxy to AI-Assist Webex messages."""
    data = await ai_assist.get_webex_messages(room_id, limit)
    if data is None:
        return {"messages": [], "count": 0}
    return data


# --- Message Linking ---

@router.post("/link", status_code=201)
async def create_link(data: LinkCreate, db: AsyncSession = Depends(get_db)) -> LinkedMessageResponse:
    valid_targets = {"project", "todo", "note"}
    if data.link_target not in valid_targets:
        raise HTTPException(400, f"Ungültiger link_target. Erlaubt: {valid_targets}")

    valid_sources = {"email", "webex"}
    if data.source not in valid_sources:
        raise HTTPException(400, f"Ungültige source. Erlaubt: {valid_sources}")

    msg = LinkedMessage(
        id=_gen_id(),
        link_target=data.link_target,
        target_id=data.target_id,
        source=data.source,
        source_ref=data.source_ref,
        subject=data.subject,
        sender=data.sender,
        date=data.date,
        snippet=data.snippet[:300] if data.snippet else "",
        snapshot=json.dumps(data.snapshot),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return _to_response(msg)


@router.delete("/link/{link_id}")
async def delete_link(link_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LinkedMessage).where(LinkedMessage.id == link_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "Verlinkung nicht gefunden")
    await db.delete(msg)
    await db.commit()
    return {"success": True}


@router.get("/links")
async def list_links(
    link_target: str | None = Query(None),
    target_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[LinkedMessageResponse]:
    stmt = select(LinkedMessage)
    if link_target:
        stmt = stmt.where(LinkedMessage.link_target == link_target)
    if target_id:
        stmt = stmt.where(LinkedMessage.target_id == target_id)
    stmt = stmt.order_by(LinkedMessage.created_at.desc())

    result = await db.execute(stmt)
    return [_to_response(m) for m in result.scalars().all()]
