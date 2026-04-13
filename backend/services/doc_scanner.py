"""
Document scanner — orchestrates parsing, chunking, and LLM-based knowledge extraction.
"""
import hashlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem, KnowledgeEdge, ProjectDocument
from models.project import Project
from services.ai_assist_client import ai_assist
from services.doc_parser import parse_docx
from services.doc_chunker import chunk_sections, DocChunk
from services.sse_hub import sse_hub

logger = logging.getLogger("projecthub.doc_scanner")

SUPPORTED_EXTENSIONS = {".docx"}

LLM_EXTRACTION_PROMPT = """Du bist ein Wissensextraktor. Analysiere den folgenden Abschnitt aus einer Projektspezifikation und extrahiere das Kernwissen.

**Dokument:** {file_name}
**Abschnitt:** {heading_path}

---
{chunk_text}
---

Antworte AUSSCHLIESSLICH als valides JSON (kein Markdown, keine Erklärung):
{{
  "title": "Prägnanter Titel (max 80 Zeichen)",
  "summary": "Zusammenfassung in 2-5 Sätzen",
  "category": "architecture|business_logic|infrastructure|process|decision|reference",
  "tags": ["tag1", "tag2"],
  "confidence": "high|medium|low",
  "key_facts": ["Fakt 1", "Fakt 2"]
}}"""


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_hash(file_path: str) -> str:
    """Compute SHA256 of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


@dataclass
class ScanProgress:
    document_id: str
    file_name: str
    phase: str  # discovering, parsing, chunking, extracting, storing
    current: int
    total: int
    current_section: str


async def discover_documents(docs_path: str) -> list[dict]:
    """Walk directory and find supported document files."""
    documents = []
    for root, _dirs, files in os.walk(docs_path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTENSIONS and not f.startswith("~$"):
                fp = os.path.join(root, f)
                documents.append({
                    "file_path": fp,
                    "file_name": f,
                    "file_type": ext.lstrip("."),
                    "file_size": os.path.getsize(fp),
                    "file_hash": _file_hash(fp),
                })
    return documents


async def scan_project_documents(
    project_id: str,
    db: AsyncSession,
    force: bool = False,
) -> dict:
    """Full document scan pipeline for a project."""
    # Get project
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project or not project.docs_path:
        return {"error": "Kein Dokumentenpfad konfiguriert", "scanned": 0}

    if not os.path.isdir(project.docs_path):
        return {"error": f"Pfad nicht erreichbar: {project.docs_path}", "scanned": 0}

    # Phase 1: Discover
    await sse_hub.emit("doc_scan_progress", {
        "project_id": project_id,
        "phase": "discovering",
        "message": "Suche Dokumente...",
    })

    found_files = await discover_documents(project.docs_path)
    total_docs = len(found_files)
    scanned_count = 0
    total_items_created = 0

    for doc_idx, file_info in enumerate(found_files):
        file_path = file_info["file_path"]
        file_hash = file_info["file_hash"]
        file_name = file_info["file_name"]

        # Check if already tracked
        existing = await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.project_id == project_id,
                ProjectDocument.file_path == file_path,
            )
        )
        doc_record = existing.scalar_one_or_none()

        if doc_record and doc_record.file_hash == file_hash and not force:
            logger.info("Skipping unchanged: %s", file_name)
            continue

        # Create or update document record
        if not doc_record:
            doc_record = ProjectDocument(
                id=_gen_id(),
                project_id=project_id,
                file_path=file_path,
                file_name=file_name,
                file_type=file_info["file_type"],
                file_size=file_info["file_size"],
                file_hash=file_hash,
                scan_status="scanning",
            )
            db.add(doc_record)
        else:
            doc_record.file_hash = file_hash
            doc_record.file_size = file_info["file_size"]
            doc_record.scan_status = "scanning"
            doc_record.updated_at = _now()
            # Delete old knowledge items from this document
            await _delete_doc_items(db, project_id, doc_record.id)

        await db.commit()

        await sse_hub.emit("doc_scan_progress", {
            "project_id": project_id,
            "document_id": doc_record.id,
            "file_name": file_name,
            "phase": "parsing",
            "current": doc_idx + 1,
            "total": total_docs,
        })

        # Phase 2: Parse
        try:
            sections = parse_docx(file_path)
        except Exception as e:
            logger.error("Parse error for %s: %s", file_name, e)
            doc_record.scan_status = "error"
            await db.commit()
            continue

        if not sections:
            doc_record.scan_status = "done"
            doc_record.total_sections = 0
            doc_record.extracted_items = 0
            doc_record.last_scanned_at = _now()
            await db.commit()
            continue

        doc_record.total_sections = len(sections)

        await sse_hub.emit("doc_scan_progress", {
            "project_id": project_id,
            "document_id": doc_record.id,
            "file_name": file_name,
            "phase": "chunking",
            "total_sections": len(sections),
        })

        # Phase 3: Chunk
        chunks = chunk_sections(sections)

        await sse_hub.emit("doc_scan_progress", {
            "project_id": project_id,
            "document_id": doc_record.id,
            "file_name": file_name,
            "phase": "extracting",
            "total_chunks": len(chunks),
            "current": 0,
        })

        # Phase 4: Extract via LLM
        items_created = 0
        created_item_ids: list[str] = []

        for chunk_idx, chunk in enumerate(chunks):
            await sse_hub.emit("doc_scan_progress", {
                "project_id": project_id,
                "document_id": doc_record.id,
                "file_name": file_name,
                "phase": "extracting",
                "current": chunk_idx + 1,
                "total_chunks": len(chunks),
                "current_section": chunk.heading_path,
            })

            extracted = await _extract_knowledge_from_chunk(chunk, file_name)
            if not extracted:
                # Fallback: use chunk as-is without LLM
                extracted = _fallback_extraction(chunk, file_name)

            # Create KnowledgeItem
            item = KnowledgeItem(
                id=_gen_id(),
                project_id=project_id,
                title=extracted["title"][:300],
                content=f"<p>{extracted['summary']}</p>",
                content_plain=extracted["summary"],
                category=extracted.get("category", "reference"),
                source_type="document",
                source_ref=doc_record.id,
                tags=json.dumps(extracted.get("tags", [])),
                confidence=extracted.get("confidence", "medium"),
                extra_data=json.dumps({
                    "heading_path": chunk.heading_path,
                    "has_diagrams": chunk.has_diagrams,
                    "key_facts": extracted.get("key_facts", []),
                    "source_headings": chunk.source_headings,
                }),
            )
            db.add(item)
            created_item_ids.append(item.id)
            items_created += 1

        # Phase 5: Store + create edges
        await db.commit()

        # FTS index for new items
        for item_id in created_item_ids:
            result = await db.execute(
                select(KnowledgeItem).where(KnowledgeItem.id == item_id)
            )
            item = result.scalar_one_or_none()
            if item:
                await _fts_insert(db, item)

        # Create BASED_ON edges between consecutive items from same doc
        for i in range(1, len(created_item_ids)):
            edge = KnowledgeEdge(
                id=_gen_id(),
                source_item_id=created_item_ids[i],
                target_item_id=created_item_ids[i - 1],
                edge_type="based_on",
                label=f"Gleiche Spezifikation: {file_name}",
            )
            db.add(edge)

        # Create RELATED edges to existing items by tag overlap
        await _create_tag_based_edges(db, project_id, created_item_ids)

        # Update document record
        doc_record.scan_status = "done"
        doc_record.extracted_items = items_created
        doc_record.last_scanned_at = _now()
        await db.commit()

        scanned_count += 1
        total_items_created += items_created

        await sse_hub.emit("doc_scan_progress", {
            "project_id": project_id,
            "document_id": doc_record.id,
            "file_name": file_name,
            "phase": "done",
            "items_created": items_created,
        })

    # Final event
    await sse_hub.emit("doc_scan_complete", {
        "project_id": project_id,
        "scanned": scanned_count,
        "total_items": total_items_created,
        "total_docs": total_docs,
    })

    await sse_hub.emit("knowledge_update", {"project_id": project_id})

    return {
        "scanned": scanned_count,
        "total_items": total_items_created,
        "total_docs": total_docs,
    }


async def _extract_knowledge_from_chunk(chunk: DocChunk, file_name: str) -> dict | None:
    """Use AI-Assist LLM to extract structured knowledge from a chunk."""
    prompt = LLM_EXTRACTION_PROMPT.format(
        file_name=file_name,
        heading_path=chunk.heading_path,
        chunk_text=chunk.full_text[:5000],  # Truncate for LLM context
    )

    try:
        result = await ai_assist.post("/api/chat", {
            "session_id": f"projecthub-docextract-{_gen_id()}",
            "message": prompt,
        })
        if not result or "response" not in result:
            return None

        response_text = result["response"]

        # Try to parse JSON from response
        # LLM might wrap in ```json ... ```
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            # Validate required fields
            if "title" in parsed and "summary" in parsed:
                # Sanitize category
                valid_categories = {"architecture", "business_logic", "infrastructure", "process", "decision", "reference"}
                if parsed.get("category") not in valid_categories:
                    parsed["category"] = "reference"
                if parsed.get("confidence") not in {"high", "medium", "low"}:
                    parsed["confidence"] = "medium"
                return parsed

    except Exception as e:
        logger.warning("LLM extraction failed for chunk '%s': %s", chunk.heading_path, e)

    return None


def _fallback_extraction(chunk: DocChunk, file_name: str) -> dict:
    """Create a basic extraction without LLM."""
    title = chunk.source_headings[0] if chunk.source_headings else chunk.heading_path
    if len(title) > 80:
        title = title[:77] + "..."

    summary = chunk.text[:500] if chunk.text else "Keine Zusammenfassung verfügbar."

    # Simple tag extraction from heading
    words = re.findall(r'\b\w{4,}\b', chunk.heading_path.lower())
    tags = list(set(words))[:5]

    return {
        "title": title,
        "summary": summary,
        "category": "reference",
        "tags": tags,
        "confidence": "low",
        "key_facts": [],
    }


async def _delete_doc_items(db: AsyncSession, project_id: str, doc_id: str):
    """Delete all knowledge items from a specific document."""
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == "document",
            KnowledgeItem.source_ref == doc_id,
        )
    )
    for item in result.scalars().all():
        # FTS delete
        try:
            row_result = await db.execute(
                text("SELECT rowid, title, content_plain, tags FROM knowledge_items WHERE id = :id"),
                {"id": item.id}
            )
            row = row_result.fetchone()
            if row:
                tags_text = " ".join(json.loads(row[3])) if row[3] else ""
                await db.execute(text("""
                    INSERT INTO knowledge_items_fts(knowledge_items_fts, rowid, title, content_plain, tags)
                    VALUES ('delete', :rowid, :title, :content_plain, :tags)
                """), {"rowid": row[0], "title": row[1], "content_plain": row[2], "tags": tags_text})
        except Exception:
            pass

        await db.delete(item)


async def _create_tag_based_edges(db: AsyncSession, project_id: str, new_item_ids: list[str]):
    """Create RELATED edges between new items and existing items with overlapping tags."""
    if not new_item_ids:
        return

    # Get new items
    new_result = await db.execute(
        select(KnowledgeItem).where(KnowledgeItem.id.in_(new_item_ids))
    )
    new_items = new_result.scalars().all()

    # Get existing items (not from this batch)
    existing_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            ~KnowledgeItem.id.in_(new_item_ids),
        )
    )
    existing_items = existing_result.scalars().all()

    for new_item in new_items:
        new_tags = set(json.loads(new_item.tags)) if new_item.tags else set()
        if not new_tags:
            continue

        for existing in existing_items:
            existing_tags = set(json.loads(existing.tags)) if existing.tags else set()
            overlap = new_tags & existing_tags
            if len(overlap) >= 2:  # At least 2 shared tags
                edge = KnowledgeEdge(
                    id=_gen_id(),
                    source_item_id=new_item.id,
                    target_item_id=existing.id,
                    edge_type="related",
                    label=f"Gemeinsame Tags: {', '.join(list(overlap)[:3])}",
                )
                db.add(edge)


async def _fts_insert(db: AsyncSession, item: KnowledgeItem):
    """Insert item into FTS5 index."""
    try:
        result = await db.execute(
            text("SELECT rowid FROM knowledge_items WHERE id = :id"),
            {"id": item.id}
        )
        row = result.fetchone()
        if row:
            tags_text = " ".join(json.loads(item.tags)) if item.tags else ""
            await db.execute(text("""
                INSERT OR REPLACE INTO knowledge_items_fts(rowid, title, content_plain, tags)
                VALUES (:rowid, :title, :content_plain, :tags)
            """), {
                "rowid": row[0],
                "title": item.title,
                "content_plain": item.content_plain,
                "tags": tags_text,
            })
            await db.commit()
    except Exception as e:
        logger.warning("FTS insert failed: %s", e)
