# Research Auto-Mode — Implementation Workflow

| Feld | Wert |
|------|------|
| Erstellt | 2026-05-16 |
| Strategie | systematic (Phase-Gates, sequenzielle Kernlinie + Parallel-Bahnen) |
| Tiefe | deep |
| Quell-Spec | `design_research_auto_mode_20260516.md` |
| Memory | `project_research_auto_mode.md` |
| Branch (vorgesehen) | `feat/research-auto-mode` (long-running) |
| Versionierung | VERSION + `main.py` Version pro Merge ([[version-bump]]) |

Dieses Dokument ist die **operative Anleitung** für die Umsetzung. Es übersetzt die 15 Phasen der Spec in konkrete Tasks, Dateien, Tests, Quality-Gates und Personas, und benennt explizit, was parallel laufen kann.

---

## 1. Workflow-Übersicht (Dependency-DAG)

```
        ╔════════════════════════════ FOUNDATIONS (sequenziell) ═════════════════════════╗
        ║                                                                                ║
        ║  P0  Extract claim_aggregation.py                                              ║
        ║   │                                                                            ║
        ║   ▼                                                                            ║
        ║  P1  Models + Run-State + ProjectResearchSettings                              ║
        ║   │                                                                            ║
        ║   ▼                                                                            ║
        ║  P2  SearchProvider ABC + Tier-1 (lokale) Provider                             ║
        ║   │                                                                            ║
        ║   ▼                                                                            ║
        ║  P3  AI-Assist-Streaming-Bridge + Tool-Blacklist                               ║
        ║   │                                                                            ║
        ║   ▼                                                                            ║
        ║  P3b BM25-Prefilter + LLM-Rerank-Service  (Retrieve-Rerank-Summarize)          ║
        ║   │                                                                            ║
        ║   ▼                                                                            ║
        ║  P3c BudgetTracker  (Token-Budget mit Kategorien + Auto-Degradation)           ║
        ╚════╤═══════════════════════════════════════════════════════════════════════════╝
             │
             ├─────────────────────┐
             ▼                     ▼
   ╔═════════════════╗  ╔═════════════════╗   ◄── PROVIDER-SPRINT (parallel)
   ║  P4  Tier-1     ║  ║  P5  Tier-2     ║
   ║  Internal-      ║  ║  Internal-      ║       (alle nutzen Rerank-Pipeline
   ║  Provider       ║  ║  Provider       ║        + BudgetTracker aus P3b/c)
   ║  (conf, email,  ║  ║  (log, code,    ║
   ║  webex, jira,   ║  ║  iq, github,    ║
   ║  handbook,      ║  ║  jenkins, mq)   ║
   ║  conf_search)   ║  ║                 ║
   ╚════════╤════════╝  ╚════════╤════════╝
            └────────────┬───────┘
                         ▼
           ╔══════════════════════════════════════╗
           ║  P6  Planner + Pipeline (Normal-Mode) ║   ◄── INTELLIGENCE-CORE
           ╚══════════════╤═══════════════════════╝
                          │
                          ▼
           ╔══════════════════════════════════════╗
           ║  P7  Lateral-Expansion (Tief-Mode)   ║   ◄── TIEFEN-KERN
           ╚══════════════╤═══════════════════════╝
                          │
                          ▼
           ╔══════════════════════════════════════╗
           ║  P8  Validation-Hookup (Tier-B/C)    ║
           ╚══════════════╤═══════════════════════╝
                          │
                          ▼
           ╔══════════════════════════════════════╗
           ║  P9  Inline-Synthesis-Hook            ║
           ╚══════════════╤═══════════════════════╝
                          │
                          ▼
           ╔══════════════════════════════════════╗
           ║  P10  Router + API + SSE-Events       ║   ◄── SERVING-LAYER
           ╚══════════════╤═══════════════════════╝
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
   ╔═════════════════╗      ╔═════════════════╗     ◄── FRONTEND (parallel)
   ║  P11  Settings  ║      ║  P12  Auto-Bar  ║
   ║       Panel UI  ║      ║   + Live-Stream  ║
   ╚════════╤════════╝      ╚════════╤════════╝
            └────────────┬───────────┘
                         ▼
           ╔══════════════════════════════════════╗
           ║  P15  E2E + Hardening                ║   ◄── GO-LIVE-GATE
           ╚══════════════════════════════════════╝

   ┌─────────────────────────────────────────────────────────────┐
   │  P13  External Provider (web, mcp_context7)  — anytime ≥ P5 │  OPTIONAL
   │  P14  MCP-Auth-Bridge (gdrive, gmail)        — anytime ≥ P3 │  OPTIONAL
   └─────────────────────────────────────────────────────────────┘
```

### Sprints-Mapping (kalendarisch)

| Sprint | Phasen | Dauer | Output |
|--------|--------|-------|--------|
| **Sprint 1** | P0 → P1 → P2 → P3 → P3b → P3c | ~6.5 d | Foundations stehen, Provider-ABC + lokale Provider live, AI-Assist-Bridge + Rerank-Service + BudgetTracker funktionieren |
| **Sprint 2** | P4 ∥ P5 | ~2.5 d | Alle Internal-Provider verfügbar (parallel zwei Branches innerhalb feat-Branches möglich) — **alle Provider verdrahten Rerank-Pipeline + BudgetTracker** |
| **Sprint 3** | P6 → P7 → P8 → P9 | ~6.5 d | Pipeline läuft Normal + Tief, validiert, optional synthetisiert |
| **Sprint 4** | P10 | ~1 d | API + SSE komplett |
| **Sprint 5** | P11 ∥ P12 | ~4.5 d | Frontend (Settings + Live-Stream) |
| **Sprint 6** | P15 | ~1.5 d | E2E + Hardening, Merge-Ready |
| **Optional** | P13, P14 | ~2 d | Extern + MCP-Auth |

**Critical-Path netto:** ~22 d (ohne Optionals). Die zwei neuen Foundations-Phasen P3b/P3c addieren ~2 d, ersparen aber in P4/P5/P6/P7/P8 jeweils Re-Work, weil das Rerank+Budget-Pattern dort schon einsatzbereit ist.

---

## 2. Pre-Flight Gates (vor P0)

Diese Punkte müssen abgehakt sein, bevor Phase 0 startet:

### 2.1 Entscheidungs-Gate (D1-D10 aus Spec §18)

| Entscheidung | Empfehlung | Wer entscheidet | Wann fällig |
|--------------|------------|-----------------|-------------|
| D1 — Erster Sprint-Provider-Set | Lokal + Confluence + Email + Webex + Jira + Code-Graph + Handbook | User | vor P4 |
| D2 — `auto_synthesise` Default | Normal: OFF, Tief: ON | bestätigt | — |
| D3 — Tier-B-Strenge | Lax: `partial` = grounded; `unsupported` = flagged | User | vor P8 |
| D4 — Settings global vs. Projekt | Beides (global default + sparse project overlay) | bestätigt | — |
| D5 — Dritter Quick-Mode? | Nein vorerst | User | offen, blockt nichts |
| D6 — Tief-Default-Hops | 2 (mit Projekt-Override auf 1) | User | vor P7 |
| D7 — Sub-Query-Edit-Dialog vor P2 | Ja, 5 s Skip-Timer | User | vor P12 |
| D8 — Token-Streaming-Events Default | OFF, opt-in | bestätigt | — |
| D9 — Lateral-Entity-Modell | Reuse `synapse_entities.py` | bestätigt | — |
| D10 — Cancel-Verhalten | Persistierte bleiben, in-flight verworfen | bestätigt | — |

→ **Blocking für Kickoff: D1, D3, D6, D7** (drei davon können auch in der Phase ihrer Relevanz fallen, müssen aber spätestens dort vorliegen).

### 2.2 Verifikations-Gate

```bash
# Smoke-Tests gegen laufende AI-Assist:
curl -s http://localhost:8000/api/v2/agent/tools | jq '.tools | length'
# expected: > 30

curl -s -X POST http://localhost:8000/api/research/classify \
  -H 'Content-Type: application/json' \
  -d '{"query":"oauth2 pkce"}' | jq '.classification'
# expected: "TECHNICAL" oder "MIXED"

curl -s -X POST http://localhost:8000/api/research/sanitize \
  -H 'Content-Type: application/json' \
  -d '{"query":"oauth2 pkce in serviceX"}' | jq '.is_safe_for_web'
# expected: bool

# ProjectHub-Synapse-Test-Baseline:
cd ProjectHub/backend
python -m pytest tests/test_synapse_communities.py tests/test_synapse_validation.py -q
# expected: 28 passed, 0 failed
```

### 2.3 Branch + Memory-Gate

- [ ] Branch `feat/research-auto-mode` erstellt (aus `main`)
- [ ] Memory `project_research_auto_mode.md` existiert (✓ erledigt 2026-05-16)
- [ ] MEMORY.md Index aktualisiert (✓ erledigt 2026-05-16)
- [ ] `VERSION` + `backend/main.py` Version-Bump als erster Commit auf Branch ([[version-bump]])

---

## 3. Phasen-Detail

### Phase 0 — Validation-Library extrahieren (1.5 d)

**Persona:** refactoring-expert + backend-architect
**Ziel:** Pure-Funktionen aus `synapse_validation.py` in ein wiederverwendbares Modul holen, ohne Synapse-Verhalten zu ändern.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 0.1 | `backend/services/claim_aggregation.py` | **neu**: 4 reine Funktionen mit Type-Hints + Docstrings |
| 0.2 | `backend/services/synapse_validation.py:25-300` | Refactor: Aufrufe an `claim_aggregation.*` delegieren; Logik raus, Wrapper rein |
| 0.3 | `backend/tests/test_claim_aggregation.py` | **neu**: 8-10 Tests für Pure-Path (Claim-only, ohne Synapse-Kontext) |
| 0.4 | `backend/tests/test_synapse_validation.py` | unverändert — Canary für Verhaltenstreue |

**Zu extrahierende Funktionen:**
- `aggregate_claim(grounding_result, critic_votes) -> ClaimAggregation`
- `compute_confidence(claim_verdicts) -> tuple[float, str]`  # (score, band)
- `decide_verdict(confidence_band, has_contradiction) -> str`  # persist|persist_flagged|human_review
- `select_verifier_models(models, k, seed=None) -> list[str]`

**Definition of Done:**
- [ ] Neues Modul existiert, 4 Funktionen exportiert
- [ ] `synapse_validation.py` ist Wrapper, kein duplizierter Code
- [ ] `pytest tests/test_synapse_validation.py tests/test_synapse_communities.py` 28/28 grün
- [ ] `pytest tests/test_claim_aggregation.py` 8+ grün
- [ ] Coverage neues Modul ≥ 90 %
- [ ] Type-Check (`mypy backend/services/claim_aggregation.py`) sauber
- [ ] Memory-Status-Update in `project_research_auto_mode.md`

**Risiken / Watchpoints:**
- Subtile Verhaltens-Drift in `decide_verdict` (Floating-Point-Vergleiche). Mitigation: Property-Test mit Parametrize.
- `select_verifier_models` cyclet bei `len(models) < k` — Verhalten muss exakt erhalten bleiben.

**Gate für nächste Phase:** Synapse-Tests stabil → P1 freigegeben.

---

### Phase 1 — Models + Run-State (0.5 d)

**Persona:** backend-architect
**Ziel:** Neue Tabellen anlegen, Schema in `Base.metadata.create_all` registrieren.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 1.1 | `backend/models/research.py` | **neu**: 4 Klassen `ResearchRun`, `ResearchSubQuery`, `ResearchFinding`, `ProjectResearchSettings` |
| 1.2 | `backend/models/__init__.py` | Import der 4 neuen Klassen (auto-create-Pflicht) |
| 1.3 | `backend/tests/test_research_models.py` | **neu**: Schema-Smoke (Felder vorhanden, Indizes, FK), 1 Roundtrip-Insert pro Modell |

**Schema-Hinweise** (siehe Spec §7.1):
- `ResearchFinding.source_ref` Index → für Idempotenz-Lookup
- `ResearchRun(project_id, status)` Composite-Index → für "already_running"-Short-Circuit
- `ResearchSubQuery(run_id, hop)` Index → für Lateral-Joins
- `ProjectResearchSettings.project_id` ist Primary-Key (1:1)

**Definition of Done:**
- [ ] 4 Tabellen werden bei Backend-Start auto-erstellt (frische SQLite-DB)
- [ ] Roundtrip-Insert pro Modell funktioniert
- [ ] `Base.metadata.create_all` löst keine Migrationswarning aus
- [ ] Type-Check sauber

**Gate:** Smoke-Test grün → P2 freigegeben.

---

### Phase 2 — SearchProvider ABC + Tier-1 (lokal) Provider (1.5 d)

**Persona:** system-architect (ABC) + backend-architect (Adapter)
**Ziel:** Stabiler Provider-Vertrag plus 4 lokale Adapter ohne externe Abhängigkeiten.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 2.1 | `backend/services/research_providers/base.py` | **neu**: `SearchProvider`-Protocol, `Finding`, `SearchProgress`, `ProviderHealth` (siehe Spec §6.2) |
| 2.2 | `backend/services/research_providers/kb_fts.py` | **neu**: wenn `brain_embedding_enabled` → ruft `services/retrieval/hybrid.py` (RRF FTS5+cosine); sonst Fallback auf FTS5-MATCH gegen `knowledge_items_fts` |
| 2.3 | `backend/services/research_providers/project_documents.py` | **neu**: FTS auf `project_documents.content_extracted`; ggf. `services/retrieval/hybrid.py` mit `source_filter` |
| 2.4 | `backend/services/research_providers/project_notes.py` | **neu**: LIKE/FTS gegen `notes.content` |
| 2.5 | `backend/services/research_providers/chat_history.py` | **neu**: Filter über `chat_messages` mit `project_id`-Scope |
| 2.6 | `backend/services/research_providers/__init__.py` | Registry-Dict `PROVIDERS: dict[str, SearchProvider]` |
| 2.7 | `backend/tests/test_research_providers_local.py` | **neu**: pro Provider 3-4 Tests (Treffer, kein-Treffer, Snippet-Trunkierung, Cancel-mid-Stream) |

**Wichtig:** Jeder lokale Provider muss `async def stream(...)` korrekt als Async-Generator implementieren, nicht als Coroutine, die eine Liste returned. Findings werden einzeln geyielded.

**Definition of Done:**
- [ ] Provider-Registry kennt 4 lokale Provider mit Default-Enabled=True
- [ ] Jeder Provider liefert auf Test-Topic ≥ 1 Finding aus Test-DB
- [ ] `health()` jedes Providers gibt `ok=True` für lokale Quellen zurück
- [ ] FTS-Query-Escape getestet (Sonderzeichen, leere Strings)
- [ ] Cancel-Event unterbricht Stream binnen < 100 ms

**Risiko:** FTS-Tabellen sind ggf. nicht für `project_documents` / `chat_messages` / `notes` indiziert — Check beim Schema-Audit; falls fehlend, Index-Migration als Sub-Task 2.0.

**Gate:** Alle lokalen Provider-Tests grün → P3 freigegeben.

---

### Phase 3 — AI-Assist-Streaming-Bridge (1 d)

**Persona:** backend-architect + security-engineer (Blacklist-Review)
**Ziel:** Generischer Helper, der einen AI-Assist-Tool-Call über `agent_stream` ausführt und Tool-Result-Events als Findings emitiert.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 3.1 | `backend/services/research_providers/_streaming.py` | **neu**: `stream_agent_tool(tool_name, args, cancel) -> AsyncIterator[SearchProgress]` |
| 3.2 | `backend/services/research_providers/_streaming.py` | Hardcoded `_TOOL_BLACKLIST = {"iq_create_waiver","jenkins_trigger_build","email_send","webex_send","mq_publish"}` mit Pre-Call-Check |
| 3.3 | `backend/services/research_providers/_streaming.py` | Result-Mapping: `tool_result` → Liste von Findings (Provider-spezifischer Parser injizierbar) |
| 3.4 | `backend/services/research_providers/_streaming.py` | Timeout pro Tool-Call (aus Settings) + Cancel-Propagation |
| 3.5 | `backend/tests/test_streaming_bridge.py` | **neu**: Fake-SSE-Generator, prüft Blacklist (5 verbotene), Tool-Result-Parse, Timeout, Cancel mid-stream |

**Sicherheits-Gate:** Code-Review durch security-engineer-Persona auf:
- Blacklist vollständig?
- Pre-Call-Check vor jedem Tool-Aufruf (nicht nur einmal pro Session)?
- Keine Bypass-Möglichkeit über kwargs?

**Definition of Done:**
- [ ] 5 Blacklist-Tools werden serverseitig abgelehnt (Test pro Tool)
- [ ] Fake-AI-Assist liefert Tool-Result → Helper yieldet ≥ 1 Finding
- [ ] Cancel via `asyncio.Event` stoppt Stream binnen < 500 ms
- [ ] Timeout > `timeout_sec` löst sauberes `failed`-Finding aus, kein Hang

**Gate:** Streaming-Bridge-Tests + Security-Review grün → P3b freigegeben.

---

### Phase 3b — BM25-Prefilter + **Rerank-Adapter (Multi-Strategy)** (1 d) — **RERANK-KERN**

**Persona:** backend-architect + performance-engineer (Batch-Effizienz)
**Ziel:** Stage-1 (BM25) als kleiner Eigen-Helper, Stage-2 als **Adapter** der zur Laufzeit die beste verfügbare Strategie wählt: Brain-Reranker (T3.x, sobald da) > Brain-Embedder (T2.2) > LLM-Eigen-Rerank > BM25-only. Keine Provider-Kopplung.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 3b.1 | `backend/requirements.txt` | `rank-bm25` hinzufügen (oder Eigen-Impl in 3b.2) |
| 3b.2 | `backend/services/research_providers/_bm25.py` | **neu**: `score_chunks(query, chunks) -> list[ScoredChunk]`. Stopword-Liste DE+EN. Sonderzeichen-Escape. |
| 3b.3 | `backend/services/research_rerank.py` | **neu**: `class RerankAdapter` mit `async rerank(query, chunks, mode, budget) -> list[ScoredChunk]` — Strategy-Switch zur Laufzeit |
| 3b.4 | `backend/services/research_rerank.py` | Strategie `bm25_embedding`: holt `LiteLLMEmbedder` aus `services/embedding/`, embedded Query + Chunks (batch), cosine sort, kein LLM-Call |
| 3b.5 | `backend/services/research_rerank.py` | Strategie `bm25_brain`: ruft (sobald da) `services/retrieval/reranker.py` — bis dahin raise NotImplementedError + automatischer Fallback auf `bm25_embedding` |
| 3b.6 | `backend/services/research_rerank.py` | Strategie `bm25_llm` (Fallback): eigener Batch-Prompt-Reranker mit JSON-Score-Parse + 1× Retry-on-malformed |
| 3b.7 | `backend/services/research_rerank.py` | `mode="auto"`-Wähler: checkt `settings.brain_reranker_enabled` + `settings.brain_embedding_enabled` + Health-Pings; cached die Wahl pro Run |
| 3b.8 | `backend/services/research_rerank.py` | Multi-Batch-Logik (alle Strategien): bei `len(chunks) > batch_size` → sequenzielle Batches, Aggregate-Sort |
| 3b.9 | `backend/services/research_rerank.py` | Integration mit BudgetTracker (Phase 3c): `await budget.reserve("rerank"\|"embedding", ...)` — beide Kategorien sind exempt |
| 3b.10 | `backend/tests/test_research_bm25.py` | Unit: Score-Ordering, Stopwords, leere Query, Sonderzeichen |
| 3b.11 | `backend/tests/test_research_rerank.py` | Unit: `mode="auto"`-Wahl, Strategie-Switch, Brain-Reranker-NotImpl-Fallback, Malformed-JSON-Retry, Multi-Batch, Embedding-Cosine korrekt |

**Performance-Review-Punkte:**
- Batch-Prompt überschreitet NICHT Context-Window des Planner-Modells (üblich 8k–32k)?
- Score-Aggregation bei Multi-Batch ist stabil (Re-Normalisierung pro Batch)?
- Bei `mode="bm25"`: Stage 2 wird **nicht** aufgerufen, kein LLM-Call?
- Bei `mode="llm_only"`: kein BM25, alle Chunks gehen in den Reranker — Cap durch `max_batches`?

**Definition of Done:**
- [ ] BM25 mit 100 Chunks läuft in < 50 ms
- [ ] Rerank-Batch mit 15 Chunks ergibt 15 Scores in einem Call
- [ ] Malformed JSON → 1× Retry → wenn auch fail → BM25-Fallback ohne Crash
- [ ] Multi-Batch (50 Chunks → 4 Batches) liefert konsistent sortierte Top-K
- [ ] Property-Test: Top-K aus Multi-Batch ist Teilmenge der Top-K aus Single-Call (bei künstlich identischen Scores)
- [ ] Tests grün

**Gate:** Rerank-Tests grün → P3c.

---

### Phase 3c — BudgetTracker (1 d) — **BUDGET-KERN**

**Persona:** backend-architect + performance-engineer (Concurrency)
**Ziel:** Async-safer Token-Budget-Tracker mit Kategorie-Counter, Reservation/Commit, Pressure-Level, Auto-Degradation-Hooks und adaptiver Erweiterung.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 3c.1 | `backend/services/research_budget.py` | **neu**: `TokenBudgetPolicy` (Pydantic-Model aus Spec §8.1) |
| 3c.2 | `backend/services/research_budget.py` | `class BudgetTracker` mit `asyncio.Lock` und Kategorie-Counters |
| 3c.3 | `backend/services/research_budget.py` | `reserve(category, est_tokens) -> ReservationResult` — exempt-Pfad für `rerank` |
| 3c.4 | `backend/services/research_budget.py` | `commit(category, actual_tokens)` (echte Usage aus AI-Assist) |
| 3c.5 | `backend/services/research_budget.py` | `pressure_level()` → `ok/warn/tight/critical/extreme/exhausted` mit profile-spezifischen Schwellen |
| 3c.6 | `backend/services/research_budget.py` | `request_extension(amount)` — max 1× pro Run, max +30 % vom hard_cap |
| 3c.7 | `backend/services/research_budget.py` | `snapshot()` → dict für `ResearchRun.token_usage` JSON-Persistierung |
| 3c.8 | `backend/services/research_budget.py` | Helper `async def _llm_call_with_budget(category, est_in, est_out, call_fn, *args, **kwargs)` |
| 3c.9 | `backend/services/research_budget.py` | Exception-Klassen: `BudgetDegradation(suggested_action)`, `BudgetExhausted` |
| 3c.10 | `backend/services/research_budget.py` | SSE-Hook: bei Pressure-Level-Übergang `sse_hub.emit("research_budget", ...)` |
| 3c.11 | `backend/tests/test_research_budget.py` | Unit: Reservation/Commit, Kategorie-Caps, Pressure-Levels |
| 3c.12 | `backend/tests/test_research_budget_degradation.py` | Property + Integration: jede Schwelle → erwartete Maßnahme |
| 3c.13 | `backend/tests/test_research_budget_concurrency.py` | Concurrent `reserve` aus 10 Coroutinen → keine Über-Reservation |

**Concurrency-Review-Punkte:**
- `_lock` umschließt sowohl Reservation als auch Commit?
- Pending-Reservations werden bei `commit` korrekt freigegeben?
- Pressure-Level-Berechnung idempotent (kein Side-Effect)?
- SSE-Emit darf den Lock nicht halten → emit nach `__aexit__`

**Definition of Done:**
- [ ] Reservation+Commit-Roundtrip funktioniert
- [ ] Rerank-Calls werden NICHT gegen Total-Cap gezählt, aber gegen Self-Limit
- [ ] Pressure-Level-Übergänge lösen Auto-Degradation-Hints aus (testbar über Hooks)
- [ ] Adaptive Erweiterung: einmal +30 % erlaubt, zweite Anfrage abgelehnt
- [ ] 10 Coroutinen × 100 Reservationen → Total stimmt exakt
- [ ] Tests grün

**Gate:** Budget-Tests grün → P4/P5 freigegeben (parallel).

---

### Phase 4 — Tier-1 Internal-Provider (2 d) — PARALLEL mit P5

**Persona:** backend-architect
**Ziel:** Sechs „heiße" externe Provider via AI-Assist-Bridge. Alle nutzen das Retrieve-Rerank-Summarize-Pattern aus P3b und das Budget-Pattern aus P3c.

**Pro Provider gilt zusätzlich** (gegenüber der ursprünglichen Beschreibung):
- Provider ruft `_bm25.score_chunks(...)` als Stage-1
- Bei `profile.rerank.mode == "bm25_llm"` ruft Provider `LLMReranker.rerank(...)` als Stage-2
- Jeder LLM-Call (Rerank, Mini-Summary) via `_llm_call_with_budget(...)` → automatische Degradation-Reaktion
- **Confluence-Sonderfall**: `confluence.py` ruft `/api/research/confluence` und überspringt P3b komplett — die Quelle liefert bereits synthetisierte `findings[]`

**Tasks:**

| # | Provider | Datei | Tool/Endpoint |
|---|----------|-------|---------------|
| 4.1 | confluence | `research_providers/confluence.py` | `POST /api/research/confluence` (httpx-stream) |
| 4.2 | confluence_search | `research_providers/confluence_search.py` | `search_confluence(query, limit, include_body)` |
| 4.3 | email | `research_providers/email.py` | `email_find(text, filter)` + `email_read` für Top-3 |
| 4.4 | webex | `research_providers/webex.py` | `webex_search_all_rooms(text)` + `webex_messages` für Kontext |
| 4.5 | jira | `research_providers/jira.py` | `find_jira(text, filter)` |
| 4.6 | handbook | `research_providers/handbook.py` | `POST /api/research/execute?sources=["handbook"]` |
| 4.7 | Registry-Update | `research_providers/__init__.py` | 6 Provider eintragen |
| 4.8 | Tests | `tests/test_research_providers_internal.py` | pro Provider 2-3 Tests mit Fake-AI-Assist |

**Pro Provider erforderlich:**
- Settings-Mapping (z.B. `confluence.spaces` aus `provider_settings` JSON ziehen)
- Health-Check (Tool-Call mit dummy query, Fail = `auth_missing`/`unreachable`)
- Result-Normalisierung auf `Finding`-Schema
- Sinnvolle `source_ref`-Generierung für Idempotenz (z.B. `email:msg-{id}`, `confluence:page-{id}`)

**Definition of Done:**
- [ ] Alle 6 Provider in Registry sichtbar via `GET /api/research/{pid}/providers`
- [ ] Health-Check funktioniert ohne Credentials → `auth_missing`-Status
- [ ] Health-Check mit Mock-Auth → `ok`-Status
- [ ] Tests mit Fake-AI-Assist grün

**Risiken:**
- Confluence-Provider hat 300 s Timeout (Spec §4) — UI muss damit umgehen können
- Email-Volltext-Pull (`email_read`) ist teuer — nur Top-3 standardmäßig

**Gate:** Alle 6 Provider mit Mock-Auth grün → P6 darf starten (sobald auch P5 grün ist).

---

### Phase 5 — Tier-2 Internal-Provider (1.5 d) — PARALLEL mit P4

**Persona:** backend-architect
**Ziel:** Sechs weitere Provider mit kleineren/spezialisierten Quellen.

**Tasks:**

| # | Provider | Datei | Tool |
|---|----------|-------|------|
| 5.1 | log_servers | `research_providers/log_servers.py` | `log_grep` + `search_logs` |
| 5.2 | code_graph | `research_providers/code_graph.py` | `graph_search(query, type, language)` |
| 5.3 | iq | `research_providers/iq.py` | `iq_findings(app_id)` |
| 5.4 | github | `research_providers/github.py` | `github_search_repos`, `github_list_prs`, `github_pr_details` |
| 5.5 | jenkins | `research_providers/jenkins.py` | `jenkins_job_status`, `jenkins_build_info` (Read-Only Check!) |
| 5.6 | mq | `research_providers/mq.py` | `mq_list_queues` + per-Queue GET |
| 5.7 | Registry-Update | `research_providers/__init__.py` | 6 weitere Provider |
| 5.8 | Tests | `tests/test_research_providers_internal2.py` | pro Provider 2 Tests |

**Sonderfall jenkins:** Provider darf NUR Read-Tools rufen, kein `jenkins_trigger_build`. Wird durch Tool-Blacklist (Phase 3) garantiert; pro Provider zusätzlicher Assertion-Test.

**Definition of Done:** wie Phase 4, für die 6 Tier-2-Provider.

**Gate:** Beide Phasen P4 + P5 grün → P6 startet.

---

### Phase 6 — Planner + Pipeline (Normal-Mode) (2 d)

**Persona:** backend-architect + performance-engineer (Concurrency-Review)
**Ziel:** Normal-Mode-Pipeline läuft Ende-zu-Ende mit allen verfügbaren Providern. Tief-Mode = noch nicht.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 6.1 | `backend/services/research_planner.py` | **neu**: `plan_subqueries(topic, depth_profile, providers, hints, kb_context, budget)` → strukturierte SubQueries via `call_json` (reuse `synapse_llm.py`) — Planner-Call läuft selbst via `_llm_call_with_budget("planning", ...)` |
| 6.2 | `backend/services/research_planner.py` | Optionaler `budget_request: int`-Pfad: bei Sub-Query mit `expected_cost="heavy"` → Planner darf einmal +30% beantragen |
| 6.3 | `backend/services/research_pipeline.py` | **neu**: `run_research(project_id, run_id)` Background-Entry (eigene async_session, never raise — Pattern aus `synapse_pipeline.py:77-89`) |
| 6.4 | `backend/services/research_pipeline.py` | BudgetTracker pro Run instanziieren aus `profile.budget`; an Provider weitergeben (Provider-ABC nimmt `budget` als Argument) |
| 6.5 | `backend/services/research_pipeline.py` | Phasen 1-5: PLAN → SEARCH-Fan-out (mit Rerank/Summary) → EXTRACT (Stub) → VALIDATE (Stub) → PERSIST |
| 6.6 | `backend/services/research_pipeline.py` | Auto-Degradation-Receiver: bei `BudgetDegradation` aus Provider/Validator → entsprechende Maßnahme ausführen (Critic-Drop, Hop-Skip, Synthesis-Skip) |
| 6.7 | `backend/services/research_pipeline.py` | SSE-Events `research_progress`, `research_subquery_started`, `research_finding`, **`research_budget`**, `research_complete` (sse_hub reuse) |
| 6.8 | `backend/services/research_pipeline.py` | Concurrency-Semaphor `asyncio.Semaphore(settings.research.max_concurrent_searches)` |
| 6.9 | `backend/services/research_pipeline.py` | Cancel-Event Wiring an alle Provider |
| 6.10 | `backend/services/research_pipeline.py` | Bei Run-Ende: `budget.snapshot()` in `ResearchRun.token_usage` persistieren |
| 6.11 | `backend/tests/test_research_pipeline_normal.py` | **neu**: vollständiger Run mit 2 Mock-Providern (Plan + Search + Persist), prüft SSE-Sequenz, Concurrency-Limit, Budget-Snapshot |
| 6.12 | `backend/tests/test_research_pipeline_budget_pressure.py` | **neu**: künstlich Budget auf 30 % drücken → Pipeline degradiert (skip Critic, skip Synthesis), `status=partial` |

**Concurrency-Review-Punkte:**
- Semaphor schließt korrekt im `finally`?
- `async_session()`-Lifecycle korrekt (eigene Session, nicht request-scoped)?
- Cancel-Event vor jedem Provider-Aufruf geprüft?
- Token-Budget-Counter atomar (kein Race)?

**Definition of Done:**
- [ ] End-to-End-Run mit 2 lokalen Providern (kb_fts + project_documents) erzeugt ≥ 1 KnowledgeItem
- [ ] SSE-Event-Sequenz korrekt (progress → subquery_started → finding → complete)
- [ ] Concurrency-Limit greift (Test mit 6 parallelen Sub-Queries, Semaphor=2 → max 2 gleichzeitig laufende)
- [ ] Cancel binnen < 1 s
- [ ] Run-Tabelle wird korrekt befüllt (Counts stimmen)

**Gate:** Normal-Mode-Run grün → P7 freigegeben.

---

### Phase 7 — Lateral-Expansion (Tief-Mode) (2 d) — **TIEFEN-KERN**

**Persona:** backend-architect + performance-engineer (Runaway-Review)
**Ziel:** Tief-Mode mit Hop-Loop, Entity-Extract, Relevance-Ranking, Lateral-Planner.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 7.1 | `backend/services/research_lateral.py` | **neu**: `expand_hop(run, findings_round_n) -> list[SubQuery]` |
| 7.2 | `backend/services/research_lateral.py` | Reuse `synapse_entities.py` für Entity-Extract (1 LLM-Call pro High-Conf-Finding, Cap 5 Entities) |
| 7.3 | `backend/services/research_lateral.py` | `filter_high_value(entities)`: `min_length=3`, Blacklist, `frequency≥2` ODER `confidence≥0.8` |
| 7.4 | `backend/services/research_lateral.py` | `rank_by_relevance(entities, topic)`: ein LLM-Call, gibt Score 0-1 pro Entität |
| 7.5 | `backend/services/research_lateral.py` | `plan_lateral_subquery(entity, topic, parents)`: ein LLM-Call → ein SubQuery, mit `is_lateral=True`, `parent_finding_ids`, `entity_focus`, `hop` |
| 7.6 | `backend/services/research_pipeline.py` | Hop-Loop nach Phase 3 wenn `depth == "tief"`: max 2 Hops, Budget-Check `llm_calls_used < 0.7 * max_llm_calls_per_run` |
| 7.7 | `backend/services/research_pipeline.py` | Neuer SSE-Event `research_lateral_planned` mit `{hop, entities, new_sub_queries}` |
| 7.8 | `backend/tests/test_research_lateral.py` | **neu**: Unit-Tests für Entity-Dedup, Relevance-Cutoff, Hop-Limit, Budget-Cap |
| 7.9 | `backend/tests/test_research_lateral_runaway.py` | **neu**: Property-Test — 100 fake Entitäten → max 6 SubQueries pro Hop, max 2 Hops, hartes Stop |
| 7.10 | `backend/tests/test_research_pipeline_tief.py` | **neu**: vollständiger Tief-Run mit Fake-Providern, 2 Hops, prüft Lineage (`parent_finding_ids` korrekt verkettet) |

**Performance/Runaway-Review-Punkte:**
- Entity-Dedup über alle Runden (Set persistiert)?
- Hop-Counter inkrementiert atomar?
- Budget-Check VOR neuer LLM-Call-Anforderung?
- Lateral-Planner-Output-Schema-Validation (Reject + Skip statt Crash)?
- `relevance_score < relevance_cutoff` → SubQuery verworfen?

**Definition of Done:**
- [ ] Tief-Run mit 2 Mock-Providern produziert: 4 initial + 3 lateral = 7 SubQueries
- [ ] `parent_finding_ids` zeigt korrekte Eltern-Kind-Kette
- [ ] Runaway-Test bestätigt 6/2-Caps
- [ ] Budget-Cap: bei künstlichem `llm_calls_used = 0.8 * max` → kein zweiter Hop
- [ ] SSE-Event `research_lateral_planned` korrekt geformt
- [ ] Cancel zwischen Hops greift binnen < 500 ms

**Gate:** Tief-Mode + Runaway-Tests grün → P8 freigegeben.

---

### Phase 8 — Validation-Hookup (1.5 d)

**Persona:** backend-architect
**Ziel:** Tier-B (LLM-as-NLI) für alle Findings, Tier-C nach Tiefen-Profil. ReviewQueue-Integration.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 8.1 | `backend/services/research_validation.py` | **neu**: `validate_finding(finding, sources, profile) -> ClaimAggregation` — nutzt `claim_aggregation.*` aus P0 |
| 8.2 | `backend/services/research_validation.py` | Claim-Zerlegung: ein LLM-Call pro Finding extrahiert atomare Claims (reuse `synapse_validation.py:117-180` Pattern) |
| 8.3 | `backend/services/research_validation.py` | Tier-B-Grounding (immer) → Tier-C nur wenn `profile.enable_critic_fanout=true` oder `tier_b.score < 0.5` oder `contradicted` |
| 8.4 | `backend/services/research_pipeline.py` | Phase 4 (VALIDATE) ruft `validate_finding` pro Finding, mappt Verdict auf `Finding.status` und `confidence_band` |
| 8.5 | `backend/services/research_pipeline.py` | Flagged → `KnowledgeReviewQueue`-Entry analog Synapse |
| 8.6 | `backend/services/research_pipeline.py` | SSE-Event `research_finding_updated` bei Statuswechsel `candidate → grounded/flagged/rejected` |
| 8.7 | `backend/tests/test_research_validation.py` | **neu**: 6-8 Tests: Tier-B-only-Pfad, Tier-C-Escalation, Contradiction-Flag, ReviewQueue-Insert |

**Definition of Done:**
- [ ] Normal-Run: nur Tier-B (außer Contradiction)
- [ ] Tief-Run: Tier-B + Tier-C immer
- [ ] Flagged Findings → KnowledgeReviewQueue
- [ ] SSE-Event-Sequenz: jedes Finding hat finalen `_updated`-Event vor `_complete`

**Gate:** Validation-Tests grün → P9 freigegeben.

---

### Phase 9 — Inline-Synthesis-Hook (1 d)

**Persona:** backend-architect
**Ziel:** Nach erfolgreichem Run optional Synapsen-Generation auf die frisch entstandenen Items triggern.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 9.1 | `backend/services/synapse_pipeline.py:77` | Erweitern: optionaler Parameter `scope_item_ids: list[str] \| None = None` |
| 9.2 | `backend/services/synapse_pipeline.py` | Wenn `scope_item_ids` gesetzt: nur diese Items + transitive Entitäten/Edges, nicht das ganze Projekt |
| 9.3 | `backend/services/research_pipeline.py` | Nach Phase 5 (PERSIST), wenn `profile.auto_synthesise=True`: `await run_synapse_generation(pid, scope_item_ids=new_item_ids)` |
| 9.4 | `backend/services/research_pipeline.py` | `ResearchRun.synapse_run_id` füllen + SSE-Event `research_complete` enthält `synapse_run_id` |
| 9.5 | `backend/tests/test_research_synthesis_hook.py` | **neu**: 3 Tests: scope-eingeschränkter Synapse-Run, Synapse-Run-ID korrekt verlinkt, kein-Auto-bei-Normal-Mode |

**Risiko:** Synapse-Pipeline ist auf Projekt-weite Operation ausgelegt. Scope-Einschränkung darf bestehendes Verhalten NICHT brechen.

**Definition of Done:**
- [ ] Tief-Run produziert auch SynapseGenerationRun-Row
- [ ] Synapse-Tests P0-stabil bleiben grün
- [ ] Normal-Run produziert KEINE Synapse-Generation (sofern Setting default)

**Gate:** Synthesis-Hook-Tests grün + Synapse-Regression-Tests grün → P10 freigegeben.

---

### Phase 10 — Router + API (1 d)

**Persona:** backend-architect + security-engineer (API-Surface-Review)
**Ziel:** Alle Endpoints aus Spec §9.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 10.1 | `backend/routers/research.py` | **neu**: 8 Endpoints (siehe Spec §9.1+§9.2) |
| 10.2 | `backend/routers/research.py` | `POST /runs`: Validierung (depth in {normal,tief}), Concurrency-Short-Circuit, `asyncio.create_task(run_research)` |
| 10.3 | `backend/routers/research.py` | `POST /runs/{id}/cancel`: setzt `asyncio.Event` über Run-State-Registry |
| 10.4 | `backend/routers/research.py` | Findings-Accept/Reject mit Note |
| 10.5 | `backend/routers/project_research_settings.py` | **neu**: GET + PUT `/settings`, Provider-Health-Aggregation |
| 10.6 | `backend/main.py` | Beide Router registrieren ([[tool-registration]] Pattern — direkt in `main.py`, nicht lifespan.py) |
| 10.7 | `backend/tests/test_research_routes.py` | **neu**: API-Tests via TestClient: 8 Endpoints, `already_running`-Pfad, Cancel-Flow, Settings-Roundtrip |

**Security-Review-Punkte:**
- Path-Param `pid` wird gegen Project-Existenz geprüft (analog `knowledge.py:933`)
- `providers_override` wird gegen `enabled_providers` validiert (kein Bypass)
- `sub_queries_override` wird auf Profile-Limits gekürzt
- `max_llm_calls_override` wird auf Profile-Limit gecappt

**Definition of Done:**
- [ ] Alle 8 Endpoints reagieren mit erwartetem Status-Code
- [ ] OpenAPI-Doc generiert korrekt (Swagger sichtbar)
- [ ] Concurrency-Short-Circuit getestet
- [ ] Cancel-Flow End-to-End

**Gate:** API-Tests grün → P11/P12 starten parallel.

---

### Phase 11 — Frontend Settings-UI (2 d) — PARALLEL mit P12

**Persona:** frontend-architect
**Ziel:** „Wissens-Quellen"-Panel im Projekt-Settings.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 11.1 | `frontend/src/lib/api/research.ts` | **neu**: API-Client für Provider/Settings/Health |
| 11.2 | `frontend/src/lib/types.ts` | Typen `Provider`, `ProviderHealth`, `ProjectResearchSettings`, `ResearchDepthProfile` |
| 11.3 | `frontend/src/stores/researchSettingsStore.ts` | **neu**: Zustand-Store für Settings + Health |
| 11.4 | `frontend/src/components/settings/SourcesPanel.tsx` | **neu**: Liste mit Toggle, Default-Settings-Forms pro Provider, Health-Badges, Test-Buttons |
| 11.5 | `frontend/src/components/settings/DepthProfileEditor.tsx` | **neu**: Form für Override der Profile-Limits |
| 11.6 | `frontend/src/components/settings/RoutingHintsEditor.tsx` | **neu**: Textarea für Routing-Hinweise |
| 11.7 | `frontend/src/components/settings/ProjectSettingsTabs.tsx` | Neuer Tab „Wissens-Quellen" eingefügt |
| 11.8 | `frontend/tests/SourcesPanel.test.tsx` | Vitest: Toggle → API-Call, Health-Refresh, Test-Button |

**Health-UX:**
- 🟢 OK
- 🔴 nicht erreichbar / auth_missing
- ⚪ nicht konfiguriert
- Health-Refresh-Knopf + Auto-Refresh alle 60 s

**Definition of Done:**
- [ ] Settings können geladen/gespeichert werden
- [ ] Pro Provider Test-Knopf zeigt Health-Result-Toast
- [ ] Default-Depth-Toggle (Normal/Tief) wirkt
- [ ] Routing-Hints werden gespeichert
- [ ] Vitest grün

**Gate:** UI-Smoke im Browser → akzeptiert.

---

### Phase 12 — Frontend Auto-Bar + Live-Stream (2.5 d) — PARALLEL mit P11

**Persona:** frontend-architect + quality-engineer (UX-Review)
**Ziel:** Live-Forschungs-View mit Phasen-Stepper, SubQuery-Strip, Findings-Stream, Lateral-Visualisierung.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 12.1 | `frontend/src/stores/researchStore.ts` | **neu**: kompletter Store (siehe Spec §13.4) + SSE-Hooks |
| 12.2 | `frontend/src/components/research/ResearchAutoBar.tsx` | **neu**: Phase-Stepper mit Lateral-Indikator |
| 12.3 | `frontend/src/components/research/SubQueryStrip.tsx` | **neu**: horizontale Liste mit Status, Lateral-Sub-Queries eingerückt |
| 12.4 | `frontend/src/components/research/FindingsStream.tsx` | **neu**: chronologische Liste, Status-Badge, Confidence, Source-Chips, Filter |
| 12.5 | `frontend/src/components/research/FindingDetail.tsx` | **neu**: Modal mit Claims, Validation-Verdicts, Lineage-Breadcrumb, Accept/Reject |
| 12.6 | `frontend/src/components/research/LateralFlowDiagram.tsx` | **neu** *(optional)*: vereinfachter Force-Graph der Hop-Beziehungen |
| 12.7 | `frontend/src/components/research/BudgetBar.tsx` | **neu**: Token-Verbrauch-Visualisierung (grün/gelb/rot Bar + Tooltip mit Kategorie-Breakdown), reagiert auf `research_budget`-SSE |
| 12.8 | `frontend/src/components/knowledge/ResearchDialog.tsx` | Erweitern: Mode-Radio (single/auto) + Depth-Radio (normal/tief) + Quellen-Auswahl-Picker |
| 12.9 | `frontend/src/components/knowledge/SubQueryEditDialog.tsx` | **neu**: nach Planner-Phase, 5 s Skip-Timer, editable SubQueries (D7) |
| 12.9 | `frontend/tests/researchStore.test.ts` | Vitest: SSE-Listener-Wiring, State-Transitions |
| 12.10 | `frontend/tests/ResearchAutoBar.test.tsx` | Vitest: Phase-Stepper, Lateral-Hop, Cancel-Button |

**UX-Review-Punkte:**
- Lateral-Beziehung visuell nachvollziehbar
- Cancel ist immer erreichbar
- Token-Stream optional (Setting)
- Findings sortierbar nach Conf + Provider + Hop

**Definition of Done:**
- [ ] Live-Stream zeigt Findings in < 200 ms nach SSE-Event
- [ ] Lateral-Sub-Queries sind als Kinder visuell erkennbar
- [ ] Cancel funktioniert mid-Run
- [ ] FindingDetail mit Validation-Breakdown sichtbar
- [ ] Vitest grün
- [ ] Browser-Smoke: Tief-Run mit 2 Hops + 7 Findings durchgeführt

**Gate:** UI funktional in Browser → P15.

---

### Phase 13 — External Provider (1 d) — OPTIONAL, anytime ≥ P5

**Persona:** backend-architect + security-engineer
**Ziel:** Web + MCP-Context7 als Drittklasse-Quellen mit Sanitization.

**Tasks:**

| # | Datei | Aktion |
|---|-------|--------|
| 13.1 | `backend/services/research_providers/web.py` | **neu**: Sanitize-Vorpfad via `/api/research/sanitize` → bei `is_safe_for_web=false` → `blocked`-Finding |
| 13.2 | `backend/services/research_providers/web.py` | Confirmation-Flow: bei `web_auto_approve=true` direkt, sonst pending → admin-confirm |
| 13.3 | `backend/services/research_providers/mcp_context7.py` | **neu**: Wrapper für `mcp__context7__query-docs` (sofern MCP-Client im Backend mountet) |
| 13.4 | Tests | `tests/test_research_providers_external.py` | Sanitize-Block, Web-Result-Parse, MCP-Smoke |

**Definition of Done:** wie Phase 4.

---

### Phase 14 — MCP-Auth-Bridge (1 d) — OPTIONAL

**Persona:** backend-architect + security-engineer
**Ziel:** Google-Drive + Gmail über MCP-Auth-Bridge.

**Status:** Spec hat es als Stub markiert. Skip im ersten Release, sofern keine konkrete Anforderung.

---

### Phase 15 — E2E + Hardening (1.5 d) — **GO-LIVE-GATE**

**Persona:** quality-engineer + backend-architect
**Ziel:** Realistische End-to-End-Szenarien, Idempotenz, Cancel, Error-Pfade pro Provider.

**Tasks:**

| # | Test | Inhalt |
|---|------|--------|
| 15.1 | `tests/test_research_e2e_normal.py` | Vollständiger Normal-Mode-Run mit Fake-AI-Assist: 4 SubQueries, 3 Provider, 9 Findings, 5 persisted, 1 flagged, 0 rejected |
| 15.2 | `tests/test_research_e2e_tief.py` | Vollständiger Tief-Mode-Run: 6 initial + 4 lateral = 10 SubQueries, 14 Findings, 10 persisted + SynapseRun-ID |
| 15.3 | `tests/test_research_idempotency.py` | Re-Run mit selbem `(topic, providers, depth)`-Hash → bestehende Findings wiederverwendet |
| 15.4 | `tests/test_research_cancel.py` | Cancel mid-Phase 2 → in-flight wird verworfen, Run-Status `cancelled`, persistierte bleiben |
| 15.5 | `tests/test_research_provider_errors.py` | Pro Provider: AI-Assist-5xx → Provider-Status `failed`, Sub-Query weiter, Run nicht abgebrochen |
| 15.6 | `tests/test_research_runaway_guard.py` | Adversariell 1000-Entity-Stream → Runaway-Guard hält |
| 15.7 | `tests/test_research_e2e_budget_pressure.py` | **neu**: künstlich Hard-Cap auf 200k setzen → Tief-Run degradiert sauber durch alle Stufen (`warn → tight → critical → extreme → exhausted`), endet mit `status=partial`, `degradations_triggered` korrekt befüllt |
| 15.8 | `tests/test_research_e2e_rerank.py` | **neu**: Provider liefert 50 fake Chunks → BM25 + LLM-Rerank greift → nur Top-K landen in Mini-Summary → Token-Budget korrekt verrechnet |
| 15.9 | `tests/test_research_e2e_adaptive_budget.py` | **neu**: Planner setzt `budget_request=150000` für eine Sub-Query → Tracker erweitert einmalig, zweite Anfrage abgelehnt, geloggt |
| 15.10 | Manual Browser-E2E | Normal- und Tief-Run mit echter AI-Assist-Instanz auf einem Test-Projekt; **Budget-Bar im UI sichtbar + reagiert auf Degradation** |
| 15.11 | Hardening | OOM-Test, Hot-Reload-Test, mehrere parallele Runs in verschiedenen Projekten |

**Definition of Done:**
- [ ] Alle E2E-Tests grün
- [ ] Manual Browser-Run mit echter AI-Assist erfolgreich (Normal + Tief)
- [ ] Cancel-Latenz < 1 s
- [ ] Synapse-Tests 28/28 weiterhin grün (Regression)
- [ ] Frontend Vitest grün
- [ ] Memory `project_research_auto_mode.md` zu Final-Status aktualisiert
- [ ] VERSION + main.py Version final bumped
- [ ] PR vorbereitet mit komplettem Changelog

**Gate:** Alle Tests grün + Manual-Browser-Smoke → Merge in `main` möglich.

---

## 4. Quality-Gates (kanonisch)

Pro Phase folgender Standard-Quality-Gate:

```
[ ] Unit-Tests grün (pytest auf Phase-Modul)
[ ] Type-Check grün (mypy auf neue Dateien)
[ ] Lint clean (ruff)
[ ] Keine Synapse-Regression (28/28 weiterhin grün)
[ ] Memory-Status-Update in project_research_auto_mode.md
[ ] Commit-Message folgt Convention
```

Phasenspezifische Zusatz-Gates (siehe Phase-Details).

---

## 5. Personas-Matrix

| Phase | Lead | Review |
|-------|------|--------|
| P0 | refactoring-expert | backend-architect |
| P1 | backend-architect | system-architect |
| P2 | system-architect | backend-architect |
| P3 | backend-architect | security-engineer |
| P3b | backend-architect | performance-engineer (Batch-Effizienz) |
| P3c | backend-architect | performance-engineer (Concurrency) |
| P4 | backend-architect | quality-engineer |
| P5 | backend-architect | quality-engineer |
| P6 | backend-architect | performance-engineer |
| P7 | backend-architect | performance-engineer |
| P8 | backend-architect | quality-engineer |
| P9 | backend-architect | quality-engineer |
| P10 | backend-architect | security-engineer |
| P11 | frontend-architect | — |
| P12 | frontend-architect | quality-engineer |
| P13 | backend-architect | security-engineer |
| P14 | backend-architect | security-engineer |
| P15 | quality-engineer | backend-architect + frontend-architect |

---

## 6. Cross-Cutting-Workstreams

### 6.1 Versionierung ([[version-bump]])

- Pro Phase-Abschluss: Patch-Bump in `VERSION` + `backend/main.py:VERSION`-Konstante.
- Sprint-Abschluss: ggf. Minor-Bump.
- Final-Merge auf `main`: Major-Feature-Eintrag im CHANGELOG.

### 6.2 Test-Strategie

| Test-Level | Wann | Wo |
|------------|------|-----|
| Unit | Pro PR | `backend/tests/test_*.py`, `frontend/tests/*.test.{ts,tsx}` |
| Integration | Pro Phase-Gate | `backend/tests/test_research_pipeline_*.py` |
| E2E | Sprint 6 | `backend/tests/test_research_e2e_*.py` (incl. budget_pressure, rerank, adaptive_budget) + Browser-Manual |
| Property | P0, P3c, P7 | spezifische Property-Tests (Budget-Ladder, Rerank-Multi-Batch, Lateral-Runaway) |
| Regression | Jede Phase | `tests/test_synapse_validation.py` + `tests/test_synapse_communities.py` |

### 6.3 Memory-Pflege

Nach jedem Phase-Gate:
- Eintrag in `project_research_auto_mode.md` aktualisieren
- Bei wichtigen Erkenntnissen: neuer Status-Block am Ende (analog Synapse-Memory)

### 6.4 Doku

- Sprint 1 Abschluss: API-Skizze in `claudedocs/` ergänzen
- Sprint 5 Abschluss: User-Guide für „Wissens-Quellen"-Panel + Auto-Mode-Workflow

---

## 7. Branch & PR-Strategie

```
main
  └─ feat/research-auto-mode (long-running)
       ├─ feat/research-auto-mode-p0
       ├─ feat/research-auto-mode-p1
       ├─ ...
       └─ feat/research-auto-mode-p15

Final-Merge nach P15:  feat/research-auto-mode → main (squash optional)
```

**Per Phase ein PR** auf `feat/research-auto-mode`:
- PR-Titel: `[research-auto-mode P<n>] <Phase-Name>`
- Description: Phase-Tasks-Liste mit Häkchen, Test-Output-Auszug
- Reviewer: Lead + Review-Persona aus §5

---

## 8. Cross-Session-Kontinuität

Wenn die Arbeit über mehrere Sessions verteilt wird:

1. **Session-Start-Ritual:**
   - Lese `MEMORY.md` Index
   - Lese `project_research_auto_mode.md` für letzten Status
   - Lese diese Workflow-MD für nächste Phase

2. **Phasen-Abschluss-Ritual:**
   - Häkchen in Workflow-MD setzen (Edit am Phase-Section)
   - Memory-Eintrag mit Status-Block ergänzen
   - VERSION-Bump + Commit

3. **Session-Pause-Ritual:**
   - Memory-Eintrag mit „Stop"-Marker + Notiz, was als Nächstes fällig ist
   - Branch in sauberem State (kein dirty WIP)

---

## 9. Artefakt-Index (was produziert wird)

| Phase | Backend-Dateien (neu) | Frontend-Dateien (neu) | Tests (neu) |
|-------|----------------------|-----------------------|-------------|
| P0 | `services/claim_aggregation.py` | — | `test_claim_aggregation.py` |
| P1 | `models/research.py` | — | `test_research_models.py` |
| P2 | `services/research_providers/{base,kb_fts,project_documents,project_notes,chat_history,__init__}.py` | — | `test_research_providers_local.py` |
| P3 | `services/research_providers/_streaming.py` | — | `test_streaming_bridge.py` |
| **P3b** | `services/research_providers/_bm25.py`, `services/research_rerank.py` | — | `test_research_bm25.py`, `test_research_rerank.py` |
| **P3c** | `services/research_budget.py` | — | `test_research_budget.py`, `test_research_budget_degradation.py`, `test_research_budget_concurrency.py` |
| P4 | 6× `services/research_providers/{confluence,confluence_search,email,webex,jira,handbook}.py` | — | `test_research_providers_internal.py` |
| P5 | 6× `services/research_providers/{log_servers,code_graph,iq,github,jenkins,mq}.py` | — | `test_research_providers_internal2.py` |
| P6 | `services/research_planner.py`, `services/research_pipeline.py` | — | `test_research_pipeline_normal.py`, `test_research_pipeline_budget_pressure.py` |
| P7 | `services/research_lateral.py` | — | `test_research_lateral.py`, `test_research_lateral_runaway.py`, `test_research_pipeline_tief.py` |
| P8 | `services/research_validation.py` | — | `test_research_validation.py` |
| P9 | (Modifikation `synapse_pipeline.py`) | — | `test_research_synthesis_hook.py` |
| P10 | `routers/research.py`, `routers/project_research_settings.py` | — | `test_research_routes.py` |
| P11 | — | `lib/api/research.ts`, `stores/researchSettingsStore.ts`, `components/settings/{SourcesPanel,DepthProfileEditor,RoutingHintsEditor}.tsx` | `SourcesPanel.test.tsx` |
| P12 | — | `stores/researchStore.ts`, `components/research/{ResearchAutoBar,SubQueryStrip,FindingsStream,FindingDetail,LateralFlowDiagram,BudgetBar}.tsx`, `components/knowledge/SubQueryEditDialog.tsx` | `researchStore.test.ts`, `ResearchAutoBar.test.tsx`, `BudgetBar.test.tsx` |
| P13 | `services/research_providers/{web,mcp_context7}.py` | — | `test_research_providers_external.py` |
| P14 | `services/research_providers/{google_drive,gmail}.py` | — | (Stub) |
| P15 | (keine) | (keine) | `test_research_e2e_{normal,tief,idempotency,cancel,provider_errors,runaway_guard,budget_pressure,rerank,adaptive_budget}.py` |

**Gesamt-Neu-Schätzung:** ~40 Backend-Dateien, ~14 Frontend-Dateien, ~23 Test-Dateien.

---

## 10. Risiko-Tracker (Workflow-Sicht)

| Risiko | Phase | Watchpoint | Mitigation |
|--------|-------|------------|------------|
| Synapse-Regression durch P0-Refactor | P0 | Nach P0-Merge | Canary-Tests 28/28 sind Pflicht-Gate |
| AI-Assist-Tool-Names drift | P3, P4, P5 | Pre-Sprint 2 | Pre-Flight `GET /api/v2/agent/tools` |
| LLM-Budget-Sprengung in Tief-Mode | P7 | Runaway-Test (15.6) | Hard-Cap-Gate, Property-Test |
| Auto-Degradation droppt zu eifrig (User wollte volle Validierung) | P3c, P15 | E2E mit echter AI-Assist | Schwellen pro Projekt überschreibbar; `degradations_triggered` im Run-Log + UI transparent |
| Rerank-Batch-Output halluziniert IDs außerhalb 1..N | P3b | Unit-Test mit adversariellem Fake-LLM | Schema-Validation + Fallback auf BM25-Score |
| Token-Schätzungen weichen stark vom Actual (drift) | P3c | Snapshot vs. Schätz-Drift Log pro Run | Bei > 30 % Drift Schätzungen recalibrieren; Commit ist Source of Truth |
| Adaptive Erweiterung wird wiederholt missbraucht | P3c | Test 15.9 | Hard-Cap auf 1 Erweiterung pro Run + max +30 % |
| SSE-Channel-Flooding bei Token-Stream | P12 | Browser-Smoke | Token-Stream Default-OFF |
| Frontend-Komponenten zu komplex | P12 | Code-Review | Splitting in kleine Komponenten erzwingen |
| Wartezeiten in Tief-Mode > 7 min | P15 | E2E mit echter AI-Assist | Heartbeat-Events alle 5 s, UI zeigt Progress |
| Confluence-Provider blockiert > 5 min | P4 | E2E | 300 s Hard-Cap aus Spec §4 |
| Provider-Health-Check spammt AI-Assist | P11 | Browser-Beobachtung | Cache 60 s im Backend |

---

## 11. Kickoff-Checkliste (zum Abhaken)

- [ ] D1, D3, D6, D7 entschieden (oder explizit "default")
- [ ] AI-Assist `/api/v2/agent/tools` Smoke OK
- [ ] AI-Assist `/api/research/{classify,sanitize}` Smoke OK
- [ ] Synapse-Baseline 28/28 grün
- [ ] Branch `feat/research-auto-mode` erstellt
- [ ] VERSION + main.py bumped als Branch-Init-Commit
- [ ] Memory-Eintrag bereitgestellt (✓)
- [ ] Workflow-MD bereitgestellt (✓ — dieses Dokument)
- [ ] Sprint-1-Slot im Kalender geblockt

---

## 12. Was passiert nach Phase 15

1. **Merge auf `main`** mit vollständigem Changelog-Eintrag
2. **Memory-Update** in `project_research_auto_mode.md`: Status auf `"abgeschlossen"`
3. **MEMORY.md-Indexeintrag** anpassen (kein "Geplant" mehr)
4. **Doku-Pass**: `claudedocs/USAGE_research_auto_mode.md` als User-Guide
5. **Retro-Eintrag**: Was gut lief, was schief, Empfehlungen für Folgesprints
6. **Folge-Backlog**:
   - Echtes NLI-Modell als `GroundingChecker` (statt LLM-as-NLI)
   - 3. Tiefen-Modus „Quick" (Single-Provider, no validation, < 30 s)
   - Hierarchische Lateral-Hops (Hop > 2 mit Aggressionsstufe)
   - Google-Drive / Gmail MCP-Bridge fertigstellen (P14)
   - Provider-spezifische Tuning-Tabs in Settings-UI

---

**Ende des Workflow-Dokuments.**
