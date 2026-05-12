"""LLM-based analyzer for staged SourceChange rows.

Takes pending changes, renders a type-specific prompt, calls AI-Assist,
parses the JSON response, and writes the analysis back to the row.

If confidence >= AUTO_ACCEPT_CONFIDENCE AND relevance == "core", the
change is auto-promoted to a KnowledgeItem (per user requirement).
"""

import json
import re
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem
from models.project import Project
from models.source_change import SourceChange
from services.ai_assist_client import ai_assist
from services.sync_prompts import render_prompt

logger = logging.getLogger("projecthub.change_analyzer")


AUTO_ACCEPT_CONFIDENCE = 0.85
AUTO_ACCEPT_RELEVANCE = "core"

VALID_RELEVANCE = {"core", "related", "irrelevant"}
VALID_CATEGORIES = {
    "architecture", "business_logic", "infrastructure",
    "process", "decision", "reference", "custom",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return secrets.token_hex(8)


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


def _validate_analysis(raw: dict) -> dict | None:
    """Coerce LLM output into the canonical shape. Drops malformed entries."""
    try:
        relevance = raw.get("relevance")
        if relevance not in VALID_RELEVANCE:
            return None
        category = raw.get("category")
        if category not in VALID_CATEGORIES:
            category = "reference"
        tags = [t for t in (raw.get("tags") or []) if isinstance(t, str)][:10]
        return {
            "relevance": relevance,
            "reason": str(raw.get("reason") or "")[:300],
            "summary": str(raw.get("summary") or "")[:1000],
            "category": category,
            "tags": tags,
            "title": str(raw.get("title") or "")[:200],
            "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0.5)))),
        }
    except Exception as e:
        logger.warning("Analysis shape validation failed: %s", e)
        return None


def _build_context(project: Project, payload: dict) -> dict:
    """Flatten commonly-used fields from payload plus project context."""
    return {
        "project_name": project.name,
        "project_description": project.description or "(keine)",
        **payload,
    }


async def _get_existing_titles(db: AsyncSession, project_id: str, limit: int = 5) -> str:
    res = await db.execute(
        select(KnowledgeItem.title)
        .where(KnowledgeItem.project_id == project_id)
        .order_by(KnowledgeItem.updated_at.desc())
        .limit(limit)
    )
    titles = [t[0] for t in res.all()]
    if not titles:
        return "(noch keine Wissenseinträge)"
    return "\n".join(f"- {t}" for t in titles)


async def analyze_change(db: AsyncSession, change: SourceChange) -> bool:
    """Analyze one change. Returns True on success (change was updated).

    On auto-accept, also creates the KnowledgeItem and links it.
    """
    # Load project context
    proj_res = await db.execute(select(Project).where(Project.id == change.project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        change.analysis_status = "error"
        change.analysis = {"error": "project_gone"}
        await db.commit()
        return False

    payload = change.payload or {}
    existing = await _get_existing_titles(db, change.project_id)

    ctx = _build_context(project, payload)
    ctx["external_ref"] = change.external_ref
    ctx["existing_titles"] = existing

    prompt = render_prompt(change.source_type, ctx)
    if not prompt:
        logger.warning("No prompt for change_type=%s", change.source_type)
        change.analysis_status = "error"
        change.analysis = {"error": f"no_prompt_for_type:{change.source_type}"}
        await db.commit()
        return False

    change.analysis_status = "analyzing"
    await db.commit()

    session_id = f"projecthub-analyze-{_gen_id()}"
    result = await ai_assist.agent_call(
        session_id=session_id,
        message=prompt,
    )

    if not result or not isinstance(result, dict):
        change.analysis_status = "error"
        change.analysis = {"error": "ai_assist_unreachable"}
        change.analyzed_at = _now()
        await db.commit()
        return False

    raw_text = result.get("response") or ""
    parsed = _extract_json(raw_text)
    if not parsed:
        change.analysis_status = "error"
        change.analysis = {"error": "no_json_in_response", "raw": raw_text[:500]}
        change.analyzed_at = _now()
        await db.commit()
        return False

    analysis = _validate_analysis(parsed)
    if not analysis:
        change.analysis_status = "error"
        change.analysis = {"error": "invalid_shape", "raw": parsed}
        change.analyzed_at = _now()
        await db.commit()
        return False

    change.analysis = analysis
    change.analysis_status = "analyzed"
    change.analyzed_at = _now()

    # Auto-accept gate (user choice: confidence >= 0.85 AND relevance == "core")
    if (
        analysis["confidence"] >= AUTO_ACCEPT_CONFIDENCE
        and analysis["relevance"] == AUTO_ACCEPT_RELEVANCE
    ):
        try:
            ki = await _promote_to_knowledge(db, change, analysis, project)
            change.knowledge_item_id = ki.id
            change.analysis_status = "accepted"
            change.auto_accepted = 1
        except Exception as e:
            logger.warning("Auto-accept failed for change %s: %s", change.id, e)
            # Leave as "analyzed" so user can accept manually
    # Irrelevant items with high confidence → dismiss automatically
    elif analysis["confidence"] >= AUTO_ACCEPT_CONFIDENCE and analysis["relevance"] == "irrelevant":
        change.analysis_status = "dismissed"
        change.auto_accepted = 1

    await db.commit()
    return True


async def _promote_to_knowledge(
    db: AsyncSession,
    change: SourceChange,
    analysis: dict,
    project: Project,
) -> KnowledgeItem:
    """Create or UPDATE a KnowledgeItem from an analyzed change.

    Merge-vs-update behavior:
    - Identity = (project_id, kb_source_type, external_ref).
    - Same external_ref seen again (e.g. PR got new commits, Jira ticket
      transitioned status, baseline after refactor) → UPDATE existing row.
    - Previously-accepted history stays linked via `knowledge_item_id` on
      the SourceChange rows.
    """
    from routers.knowledge import _fts_insert, _fts_update  # local import avoids cycle

    # Idempotency: analyze_change() may call us twice — short-circuit on 2nd call
    if change.knowledge_item_id:
        existing = await db.execute(
            select(KnowledgeItem).where(KnowledgeItem.id == change.knowledge_item_id)
        )
        found = existing.scalar_one_or_none()
        if found:
            return found

    payload = change.payload or {}
    body_parts = [
        f"<p><strong>Quelle:</strong> {change.external_ref}</p>",
        f"<p><strong>Bewertung:</strong> {analysis['relevance']} — {analysis['reason']}</p>",
        f"<p>{analysis['summary']}</p>",
    ]
    if payload.get("body_snippet"):
        body_parts.append(f"<hr/><p>{payload['body_snippet']}</p>")
    content_html = "\n".join(body_parts)
    content_plain = f"{analysis['summary']} {analysis['reason']} {payload.get('body_snippet', '')}"[:5000]

    # Map change_type → knowledge source_type
    source_type_map = {
        "pr": "pr_extract",
        "build": "build_extract",
        "commit": "commit_extract",
        "commit_batch": "commit_extract",
        "codebase_baseline": "document",
        "jira": "jira_extract",
        "jira_comment": "jira_extract",
        "pr_comment": "pr_extract",
    }
    kb_source_type = source_type_map.get(change.source_type, "manual")
    title = analysis["title"] or f"{change.source_type}: {change.external_ref}"
    tags_json = json.dumps(analysis["tags"])
    confidence_label = "high" if analysis["confidence"] >= 0.85 else "medium"

    # Try to find an existing KnowledgeItem for this external_ref
    existing_res = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project.id,
            KnowledgeItem.source_type == kb_source_type,
            KnowledgeItem.source_ref == change.external_ref,
        )
    )
    existing_item = existing_res.scalar_one_or_none()

    if existing_item:
        # UPDATE path — keep id, history, any existing edges
        existing_item.title = title
        existing_item.content = content_html
        existing_item.content_plain = content_plain
        existing_item.category = analysis["category"]
        existing_item.tags = tags_json
        existing_item.confidence = confidence_label
        existing_item.updated_at = _now()
        # Preserve previous analyses in a running history
        try:
            prev_extra = json.loads(existing_item.extra_data) if existing_item.extra_data else {}
        except Exception:
            prev_extra = {}
        history = prev_extra.get("history") or []
        history.append({
            "at": _now(),
            "source_change_id": change.id,
            "payload_hash": change.payload_hash,
            "analysis": analysis,
        })
        existing_item.extra_data = json.dumps({
            **prev_extra,
            "external_ref": change.external_ref,
            "source_change_id": change.id,
            "analysis": analysis,
            "history": history[-10:],  # keep last 10 only
        })
        await db.flush()
        try:
            await _fts_update(db, existing_item)
        except Exception as e:
            logger.debug("FTS update after merge failed: %s", e)
        logger.info("Merged change %s into existing knowledge item %s", change.id, existing_item.id)
        return existing_item

    # INSERT path — fresh knowledge item
    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project.id,
        title=title,
        content=content_html,
        content_plain=content_plain,
        category=analysis["category"],
        source_type=kb_source_type,
        source_ref=change.external_ref,  # external identity (not payload hash)
        tags=tags_json,
        confidence=confidence_label,
        extra_data=json.dumps({
            "source_change_id": change.id,
            "external_ref": change.external_ref,
            "analysis": analysis,
            "history": [],
        }),
    )
    db.add(item)
    await db.flush()
    await _fts_insert(db, item)
    return item


async def analyze_pending_changes(
    db: AsyncSession,
    project_id: str,
    limit: int = 25,
) -> dict:
    """Analyze a batch of pending changes for a project.

    Returns:
        {"analyzed": N, "auto_accepted": M, "auto_dismissed": K, "errors": E}
    """
    res = await db.execute(
        select(SourceChange)
        .where(
            SourceChange.project_id == project_id,
            SourceChange.analysis_status == "pending",
        )
        .order_by(SourceChange.detected_at.desc())
        .limit(limit)
    )
    pending = list(res.scalars().all())

    counts = {"analyzed": 0, "auto_accepted": 0, "auto_dismissed": 0, "errors": 0}
    for change in pending:
        try:
            ok = await analyze_change(db, change)
            if not ok:
                counts["errors"] += 1
                continue
            counts["analyzed"] += 1
            if change.analysis_status == "accepted":
                counts["auto_accepted"] += 1
            elif change.analysis_status == "dismissed":
                counts["auto_dismissed"] += 1
        except Exception as e:
            logger.warning("Analyzer crashed on change %s: %s", change.id, e)
            counts["errors"] += 1
    return counts
