import json
import re
import secrets
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/ai", tags=["ai"])
logger = logging.getLogger("projecthub.ai")


# --- Schemas -----------------------------------------------------------------

class ProjectRef(BaseModel):
    id: str
    name: str


class ParseTodoContext(BaseModel):
    current_project_id: str | None = None
    now: str | None = None
    available_projects: list[ProjectRef] = []


class ParseTodoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    context: ParseTodoContext | None = None


class ParseTodoResponse(BaseModel):
    title: str
    description: str | None = None
    priority: Literal["high", "medium", "low"] = "medium"
    deadline: str | None = None
    tags: list[str] = []
    assignee_hint: str | None = None
    project_id: str | None = None
    confidence: float = 0.0
    used_fallback: bool = False


class GenerateRequest(BaseModel):
    mode: Literal["continue", "summarize", "improve", "shorten", "expand", "fix_grammar", "custom"]
    text: str = Field(..., min_length=1, max_length=20_000)
    prompt: str | None = None
    context: str | None = None


class GenerateResponse(BaseModel):
    text: str


# --- Prompt-Templates --------------------------------------------------------

_PARSE_SYSTEM_PROMPT = """Du hilfst einem Projekt-Management-Tool, aus einer kurzen Freitext-Notiz ein strukturiertes Todo zu extrahieren.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt in folgendem Schema — kein Markdown, kein Kommentar, nur JSON:

{
  "title": "Kurzer Titel, max. 80 Zeichen",
  "description": "Optional: Zusatzkontext, der nicht in den Titel passt. null wenn nichts.",
  "priority": "high" | "medium" | "low",
  "deadline": "YYYY-MM-DDTHH:MM:SS oder null",
  "tags": ["tag1", "tag2"],
  "assignee_hint": "@name oder 'me' oder null",
  "project_id": "ID aus Projektliste, oder null",
  "confidence": 0.0-1.0
}

Regeln:
- Priorität: "dringend", "asap", "sofort" → high. Nichts explizit → medium.
- Deadline: Relative Zeit ("morgen", "nächste Woche", "am Dienstag") in absolutes Datum wandeln, basierend auf NOW.
- Tags: Wörter mit # oder klare Kategorien ("Frontend", "Bug") — kleinbuchstaben, ohne #.
- Assignee: @-Mentions als-ist.
- project_id: Nur setzen, wenn der Input KLAR auf ein Projekt aus der untenstehenden Liste verweist (z. B. "für Projekt Foo", "im Backend-Projekt"). Gib GENAU die ID aus der Liste zurück — keine Halluzinationen, keine erfundenen IDs. Bei Unsicherheit: null.
- Confidence: 1.0 wenn Input klar ("erinnere mich an PR-Review morgen"), 0.3 wenn vage ("irgendwas mit Daten"), 0.0 wenn unsinnig.

NOW: {now}
Aktuelles Projekt-ID (URL-Kontext): {project_id}

Verfügbare Projekte ({project_count}):
{project_list}

INPUT:
"""

_GENERATE_SYSTEM_PROMPTS: dict[str, str] = {
    "continue": "Du bist ein Schreibassistent. Setze den folgenden Text direkt fort — liefere NUR den Fortsetzungstext (1-3 Sätze), keine Wiederholung, keinen Meta-Kommentar.",
    "summarize": "Fasse den folgenden Text in einem kurzen Absatz (max. 3 Sätze) zusammen. Liefere NUR die Zusammenfassung, keine Einleitung.",
    "improve": "Verbessere den folgenden Text stilistisch und grammatikalisch, ohne Inhalt hinzuzufügen oder wegzunehmen. Liefere NUR den verbesserten Text.",
    "shorten": "Kürze den folgenden Text auf das Wesentliche. Liefere NUR den gekürzten Text.",
    "expand": "Erweitere den folgenden Text um relevante Details. Liefere NUR den erweiterten Text.",
    "fix_grammar": "Korrigiere Grammatik, Rechtschreibung und Zeichensetzung. Inhalt und Stil beibehalten. Liefere NUR den korrigierten Text.",
    "custom": "",
}


# --- Helpers -----------------------------------------------------------------

def _gen_session_id() -> str:
    return f"pm-parse-{secrets.token_hex(6)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _fallback_parse(prompt: str) -> ParseTodoResponse:
    tags = re.findall(r"#([A-Za-z0-9_-]+)", prompt)
    priority: Literal["high", "medium", "low"] = "medium"
    lower = prompt.lower()
    if any(w in lower for w in ("dringend", "asap", "sofort", "!!!")):
        priority = "high"
    title = re.sub(r"#\S+", "", prompt).strip()
    if len(title) > 80:
        title = title[:77] + "…"
    return ParseTodoResponse(
        title=title or prompt[:80],
        priority=priority,
        tags=[t.lower() for t in tags],
        confidence=0.2,
        used_fallback=True,
    )


# --- Routes ------------------------------------------------------------------

@router.post("/parse-todo", response_model=ParseTodoResponse)
async def parse_todo(req: ParseTodoRequest) -> ParseTodoResponse:
    ctx = req.context or ParseTodoContext()
    now = ctx.now or _now_iso()
    valid_ids = {p.id for p in ctx.available_projects}
    if ctx.available_projects:
        project_list = "\n".join(f"- {p.id} : {p.name}" for p in ctx.available_projects)
    else:
        project_list = "(keine)"
    system = (
        _PARSE_SYSTEM_PROMPT
        .replace("{now}", now)
        .replace("{project_id}", ctx.current_project_id or "keine")
        .replace("{project_count}", str(len(ctx.available_projects)))
        .replace("{project_list}", project_list)
    )
    message = f"{system}\n{req.prompt}"

    try:
        result = await ai_assist.agent_call(
            session_id=_gen_session_id(),
            message=message,
            # Domain-detection picks tools the engine might use to enrich
            # the answer — useless for pure JSON-extraction. Disabling
            # keeps the prompt short and the response deterministic.
            auto_detect=False,
        )
    except Exception as e:
        logger.warning("AI-Assist parse-todo Aufruf fehlgeschlagen: %s", e)
        return _fallback_parse(req.prompt)

    if not result or not isinstance(result, dict):
        return _fallback_parse(req.prompt)

    raw_text = result.get("response") or ""
    parsed = _extract_json(raw_text)
    if not parsed:
        logger.info("parse-todo: LLM-Antwort ohne JSON, fallback. Raw=%r", raw_text[:200])
        return _fallback_parse(req.prompt)

    try:
        raw_project_id = parsed.get("project_id")
        project_id = raw_project_id if isinstance(raw_project_id, str) and raw_project_id in valid_ids else None
        return ParseTodoResponse(
            title=(parsed.get("title") or req.prompt[:80]).strip(),
            description=parsed.get("description") or None,
            priority=parsed.get("priority") if parsed.get("priority") in ("high", "medium", "low") else "medium",
            deadline=parsed.get("deadline") or None,
            tags=[t for t in (parsed.get("tags") or []) if isinstance(t, str)][:10],
            assignee_hint=parsed.get("assignee_hint") or None,
            project_id=project_id,
            confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
            used_fallback=False,
        )
    except Exception as e:
        logger.warning("parse-todo: Validierung fehlgeschlagen: %s, Raw=%r", e, parsed)
        return _fallback_parse(req.prompt)


def _build_generate_message(req: GenerateRequest) -> str:
    system = _GENERATE_SYSTEM_PROMPTS.get(req.mode, "")
    if req.mode == "custom":
        if not req.prompt:
            raise HTTPException(status_code=400, detail="prompt erforderlich im custom-Modus")
        system = req.prompt

    parts: list[str] = []
    if system:
        parts.append(system)
    if req.context:
        parts.append(f"Kontext:\n{req.context}")
    parts.append(f"Text:\n{req.text}")
    return "\n\n".join(parts)


@router.post("/generate", response_model=GenerateResponse)
async def generate_text(req: GenerateRequest) -> GenerateResponse:
    message = _build_generate_message(req)

    try:
        result = await ai_assist.agent_call(
            session_id=_gen_session_id(),
            message=message,
            auto_detect=False,
        )
    except Exception as e:
        logger.warning("AI-Assist generate Aufruf fehlgeschlagen: %s", e)
        raise HTTPException(status_code=503, detail="AI-Assist nicht erreichbar")

    if not result or not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="AI-Assist lieferte leere Antwort")

    text = (result.get("response") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="AI-Assist lieferte leeren Text")

    return GenerateResponse(text=text)


@router.post("/generate/stream")
async def generate_text_stream(req: GenerateRequest):
    """Streams generated text as typed SSE events (token/done/error).

    Format matches what ``frontend/src/lib/aiStream.ts`` parses: typed
    SSE with ``event: <type>`` and JSON ``data:`` payloads. The v2
    upstream produces the same event names, but with raw-string TOKEN
    payloads — we wrap them in ``{"token": "..."}`` for the frontend.
    """
    message = _build_generate_message(req)

    _TERMINAL_LABELS = {
        "cancelled": "Abgebrochen",
        "max_iterations": "Maximale Iterationen erreicht",
        "confirm_required": "Bestätigung erforderlich",
    }

    async def event_source():
        try:
            async for event in ai_assist.agent_stream(
                session_id=_gen_session_id(),
                message=message,
                extra={"auto_detect": False},
            ):
                etype = event.get("type")
                payload = event.get("data")
                if etype == "token" and isinstance(payload, str) and payload:
                    yield {
                        "event": "token",
                        "data": json.dumps({"token": payload}),
                    }
                elif etype == "done":
                    yield {"event": "done", "data": json.dumps({"done": True})}
                    return
                elif etype == "error":
                    err = (
                        payload.get("error")
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": err or "unbekannter Fehler"}),
                    }
                    return
                elif etype in _TERMINAL_LABELS:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": _TERMINAL_LABELS[etype]}),
                    }
                    return
        except Exception as e:
            logger.warning("AI-Assist v2-Stream fehlgeschlagen: %s", e)
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_source())
