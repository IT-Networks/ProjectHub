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

def _normalize_email(raw: dict) -> dict:
    """Übersetzt die AI-Assist-E-Mail-Form in die ProjectHub-Frontend-Form.

    AI-Assist liefert ``email_id`` / ``preview``; das Frontend (Store +
    InboxPage) erwartet ``id`` / ``body_preview``. Diese Übersetzung passiert
    zentral hier, damit der Feldname-Mismatch nicht über drei Schichten
    durchschlägt (er war die Ursache für React-Key-Kollisionen und die leere
    Detail-Ansicht).
    """
    return {
        "id": raw.get("email_id") or raw.get("id") or "",
        "subject": raw.get("subject") or "",
        "sender": raw.get("sender") or "",
        "sender_name": raw.get("sender_name") or "",
        "date": raw.get("date") or "",
        "body_preview": raw.get("preview") or raw.get("body_preview") or "",
        "has_attachments": bool(raw.get("has_attachments")),
        "folder": raw.get("folder") or "",
    }


@router.get("/emails")
async def search_emails(
    query: str = Query(""),
    sender: str = Query(""),
    subject: str = Query(""),
    folder: str = Query("inbox"),
    limit: int = Query(20),
):
    """Proxy to AI-Assist email search (mit Feld-Normalisierung)."""
    data = await ai_assist.post("/api/email/search", {
        "query": query,
        "sender": sender,
        "subject": subject,
        "folder": folder,
        "limit": limit,
    })
    if data is None:
        return {"success": False, "results": [], "total": 0, "error": "AI-Assist nicht erreichbar"}

    raw_results = data.get("results") or []
    results = [_normalize_email(r) for r in raw_results]
    return {
        "success": data.get("success", True),
        "results": results,
        "total": data.get("total", len(results)),
        "error": data.get("error"),
    }


@router.get("/emails/{email_id}")
async def read_email(email_id: str, folder: str = Query("inbox")):
    """Proxy to AI-Assist email read (mit Feld-Normalisierung)."""
    data = await ai_assist.get(f"/api/email/read/{email_id}", params={"folder": folder})
    if data is None:
        raise HTTPException(503, "AI-Assist nicht erreichbar")

    email = data.get("email") or {}
    return {
        "success": data.get("success", True),
        "error": data.get("error"),
        "email": {
            "id": email.get("email_id") or email.get("id") or email_id,
            "subject": email.get("subject") or "",
            "sender": email.get("sender") or "",
            "sender_name": email.get("sender_name") or "",
            "to": email.get("to") or [],
            "cc": email.get("cc") or [],
            "date": email.get("date") or "",
            "body_text": email.get("body_text") or "",
            "body_html": email.get("body_html") or "",
            "attachments": email.get("attachments") or [],
            "folder": email.get("folder") or folder,
        },
    }


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
