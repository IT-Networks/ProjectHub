# Design-Spec: Confluence-Deep-Research & parallele Dokumentenanalyse

**Datum:** 2026-05-14
**Personas:** Architect + Backend
**Status:** Design freigegeben → Implementierung
**Repos:** AI-Assist (v2.52.3), ProjectHub (v1.2.1)

---

## 1. Ausgangslage (aus /sc:analyze)

ProjectHubs „Thema recherchieren" hat **keinerlei** Confluence-spezifischen Code — es schickt
Freitext an einen generischen Agenten. Splitting + paralleles Zusammenfassen großer PDF/Word
existiert nur als Einzelteile und ist für den Confluence-Pfad nirgends verdrahtet.

### Defekt-Tabelle

| # | Befund | Ort | Schwere |
|---|--------|-----|---------|
| 1 | ProjectHub-Research hat 0 Confluence-Logik, nur generischer Agent-Call | `knowledge.py:895` | Hoch |
| 2 | `list_confluence_pdfs` / `read_confluence_pdf` referenziert, aber nie implementiert | `confluence_provider.py:213-216` | Hoch |
| 3 | `ResearchOrchestrator` (parallele Agenten) im Docstring beschrieben, Datei fehlt | `knowledge_collector/__init__.py:6` | Hoch |
| 4 | doc_scanner parst kein PDF, obwohl `.pdf` als gültig beworben | `doc_scanner.py:25` vs `knowledge.py:772` | Mittel |
| 5 | Chunk-Extraktion sequenziell statt parallel | `doc_scanner.py:216` | Mittel |
| 6 | `read_confluence` ignoriert Page-Attachments komplett | `read_confluence.py` | Mittel |
| 7 | `_search_confluence` nutzt nur Excerpt, nie Body/Attachments | `research_router.py:578` | Mittel |
| 8 | Research-Ergebnis auf 5000 Zeichen abgeschnitten | `knowledge.py:974` | Niedrig |

---

## 2. Zielarchitektur — zwei unabhängige Tracks

```
TRACK A — ProjectHub lokaler Doc-Scan (Defekte #4 #5 #8)
  doc_scanner ──┬── parse_docx ──┐
                └── parse_pdf  ──┴─→ doc_chunker ─→ [Fan-out Executor] ─→ KnowledgeItems
                   (NEU)                            (asyncio.gather+Sem)

TRACK B — AI-Assist Confluence Deep-Research (Defekte #1 #2 #3 #6 #7)
  ProjectHub /research  ──HTTP──→  POST /api/v2/research/confluence
                                          │
                                   ConfluenceResearchOrchestrator (NEU)
                                   │  1. resolve page (id/url/title)
                                   │  2. crawl child tree (bounded)
                                   │  3. pro Knoten: body + attachments
                                   ├──────────────┬──────────────┐  Fan-out
                                   ▼              ▼              ▼  (bounded gather)
                              ReadNode       ReadNode       ReadNode
                              page+pdf       page+pdf       page+pdf
                                   │              │              │
                                   └──────────────┴──────────────┘
                                          ▼
                                   KnowledgeSynthesizer (vorhanden)
                                          ▼
                                   ResearchResult → ProjectHub KnowledgeItem(s)
```

**Leitprinzip:** Die wiederverwendbare Fähigkeit „Dokument → strukturiertes Wissen" gehört nach
AI-Assist (dort liegen `confluence_client`, `read_pdf`-Triage, LLM-Client). ProjectHub bleibt Consumer.

---

## 3. Komponenten

### C1 — Confluence-Attachment-Tools (AI-Assist, #2 #6)
- `app/tools/confluence/list_confluence_pdfs.py` — `@tool` über `client.get_page_attachments()`
- `app/tools/confluence/read_confluence_pdf.py` — lädt Attachment-Bytes, delegiert an geteilte PDF-Extraktion
- Refactor-Vorbedingung: `app/tools/documents/pdf.py` → `_extract_from_reader(reader, ...)` herauslösen

### C2 — Source-agnostischer Chunker (AI-Assist, #4)
- Kanonische Kopie `doc_parser.py` + `doc_chunker.py` → `app/services/document/`
- Neu `pdf_sections.py`: `pdf_pages_to_sections()` + `html_to_sections()`

### C3 — Paralleler Extraktions-Executor (#5)
- Track A: `asyncio.gather` + `Semaphore(doc_scan_concurrency=4)` in `doc_scanner`
- Track B: Fan-out je Knoten im Orchestrator, `Semaphore(research_concurrency=3)`
- SSE-Progress nach Fertigstellung emittieren (Lock-geschützter Counter)

### C4 — ConfluenceResearchOrchestrator (AI-Assist, #1 #3 #7)
- Neu `app/agent/knowledge_collector/orchestrator.py`
- Phasen: Discovery (`ConfluenceProvider.discover`) → Planning (`ResearchPlan`) →
  Execution (Fan-out, `read_confluence`/`list_confluence_pdfs`/`read_confluence_pdf` →
  chunk → LLM-Extraktion → `ResearchFinding[]`) → Synthesis (`KnowledgeSynthesizer`)
- `gather(return_exceptions=True)` — Teilfehler nicht-fatal

### C5 — Route + ProjectHub-Anbindung (#1 #8)
- AI-Assist: `POST /api/v2/research/confluence` (eigene Route, kein Agent-Stream)
- ProjectHub: `research_to_knowledge` Umbau → Multi-Item-Mapping, `ai_assist_client.research_confluence()`
- ProjectHub: `ResearchDialog.tsx` Confluence-Ziel-Feld + „Unterseiten einbeziehen"

---

## 4. API-Spezifikation

### `POST /api/v2/research/confluence` (AI-Assist, neu)

```jsonc
// Request
{
  "topic": "string (required, min 3)",
  "page_id": "string | null",
  "url": "string | null",
  "space_key": "string | null",
  "include_children": true,
  "max_depth": 2,
  "max_pages": 15,
  "session_id": "string | null"
}
// Response 200
{
  "topic": "string", "summary": "string", "markdown": "string",
  "findings": [{ "fact","category","confidence","source_page_id",
                 "source_title","source_url","source_type" }],
  "pages_analyzed": 12, "pdfs_analyzed": 4,
  "providers": ["confluence"], "errors": [{"page_id","error"}]
}
// 422 kein Ziel auflösbar · 502 Confluence n/a · 504 Timeout
```

### `POST /api/knowledge/{project_id}/research` (ProjectHub, erweitert, abwärtskompatibel)

```jsonc
{ "topic": "string", "team": "string|null",
  "confluence_page_url": "string|null", "confluence_space": "string|null",
  "include_children": false }
// Response: KnowledgeItemResponse ODER list[KnowledgeItemResponse]
```

---

## 5. Datenmodell

Keine DB-Migration nötig. `KnowledgeItem.source_type="confluence"` existiert; `extra_data` (JSON)
nimmt `{page_id, page_url, space, finding_confidence}`. `source_ref` = Page-ID-basierter Hash.
`ProjectDocument.file_type="pdf"` wird erstmals real genutzt (Spalte existiert).

Neue Settings:
- AI-Assist `config.yaml`: `knowledge_base.research_concurrency` (3), `research_timeout` (300),
  `confluence_research_max_pages` (15)
- ProjectHub: `doc_scan_concurrency` (4)

---

## 6. Workstreams

| WS | Inhalt | Track | Aufwand | Gate |
|----|--------|-------|---------|------|
| WS-1 | Refactor `pdf.py` → `_extract_from_reader()` | B | 0,5 d | — |
| WS-2 | `list_confluence_pdfs` + `read_confluence_pdf` Tools | B | 1,5 d | WS-1 |
| WS-3 | Chunker → `app/services/document/` + `pdf_sections.py` | B | 1 d | — |
| WS-4 | `ConfluenceResearchOrchestrator` | B | 3 d | WS-2, WS-3 |
| WS-5 | Route `/api/v2/research/confluence` | B | 1 d | WS-4 |
| WS-6 | ProjectHub `doc_scanner` PDF + Parallelisierung | A | 2 d | — |
| WS-7 | ProjectHub `research_confluence` Client + Endpoint-Umbau | A/B | 1,5 d | WS-5 |
| WS-8 | ProjectHub `ResearchDialog`-UI | A | 0,5 d | WS-7 |
| WS-9 | E2E + Test-Suite | beide | 2 d | WS-6, WS-8 |

Kritischer Pfad WS-1→2→4→5→7→9 ≈ 9,5 d. Track A parallel. Strangler-Fig: alter Pfad bleibt Fallback.

---

## 7. Risiken

| Risiko | Mitigation |
|--------|-----------|
| Confluence-Rate-Limit bei Fan-out | `research_concurrency=3`; `confluence_cache` vorschalten |
| LLM-Quota bei vielen Chunks | `max_pages`-Cap; Synthese-Timeout |
| Scan-PDFs ohne Textschicht | `read_pdf`-Triage erkennt → „low confidence"-Finding statt Fehler |
| `doc_chunker`-Duplikat driftet | Cleanup-Ticket; identische Datei + Test-Parität |
| Nicht-deterministische Ergebnisse | Orchestrator gegen Mock-Client + Mock-LLM testen |

---

## 8. Test-Strategie

- Unit: `_extract_from_reader` Parität · Confluence-Tools gegen Mock-Client ·
  `pdf_pages_to_sections`/`html_to_sections` → `chunk_sections` Invarianten ·
  Orchestrator (Discovery-Cap, `return_exceptions`, Leer-Fall)
- Integration: Route happy/422/502 · ProjectHub PDF-Scan Determinismus (Edge-Reihenfolge)
- E2E: `ResearchDialog` → Confluence-Ziel → KnowledgeItems im Graph

---

## 9. Implementierungs-Notizen (2026-05-14, umgesetzt)

Abweichungen gegenüber dem ursprünglichen Design — bewusst getroffen während WS-1…9:

| # | Design sagte | Umgesetzt | Begründung |
|---|--------------|-----------|------------|
| 1 | Route `POST /api/v2/research/confluence` | `POST /api/research/confluence` | Bestehender Research-Router (`app/api/routes/research.py`, Prefix `/api/research`) ist schon in `main.py` registriert — kein neuer v2-Router nötig. |
| 2 | Neue Config-Keys `research_concurrency`/`research_timeout`/`confluence_research_max_pages` | Reuse von `knowledge_base.{max_crawl_depth, max_pages_per_research, max_parallel_agents}` | Felder existieren bereits in der Config — keine Schema-Änderung. Timeout: Modul-Konstante `_CONFLUENCE_RESEARCH_TIMEOUT=300` in `research.py`. |
| 3 | Response ggf. `list[KnowledgeItemResponse]` (Multi-Item) | **Ein** synthetisiertes `KnowledgeItem` (Markdown→HTML), Findings in `extra_data` | Response-Typ bleibt stabil → kein Frontend-Bruch. Multi-Item-Mapping = risikoarmer Folge-Schritt. |

Nachgezogene Korrekturen (Post-Review, gleicher Tag):

- **Source-Type-Attribution:** Page- und PDF-Sektionen werden im Orchestrator **getrennt** gechunkt (`chunk_sections(sections)` + `chunk_sections(pdf_sections)`), jeder Chunk-Batch trägt `source_type` explizit — keine `heading_path`-Heuristik mehr (vermied Fehlattribution bei gemergten Chunks).
- **LLM-Timeout:** Extraktion nutzt `chat_with_tools(..., timeout=60s)` statt `chat_quick` (15 s) — tool-grade Call, nicht „quick classification".
- **Markdown-Rendering:** ProjectHub konvertiert die Synthese-Markdown serverseitig nach HTML (`_markdown_to_html`, lib `markdown`), da `NodeDetailPanel` `content` via `dangerouslySetInnerHTML` rendert.

Verifikation: 117 Unit/Integration-Tests grün. Echter Service-Boundary-Test via
`scripts/smoke_confluence_research.py` (benötigt laufende AI-Assist-Instanz + Confluence).

Offene Folge-Tickets: `doc_chunker`-Dedup · Multi-Item-Mapping · SSE-Progress (Orchestrator
hat `progress_cb`, ProjectHub-Seite noch nicht verdrahtet).
