# Research Auto-Mode — Design Specification

| Feld | Wert |
|------|------|
| Erstellt | 2026-05-16 |
| Letzte Anpassung | 2026-05-18 (Brain-Konsument-Modell) |
| Status | Draft → Review |
| Scope | ProjectHub Research-Pipeline Redesign |
| Verwandte Dokumente | `design_confluence_deep_research_20260514.md` · `bridge_openapi_20260516.yaml` · `design_memory_systems_20260516.md` (Brain-Master) · Memory `project_synapsen_knowledge_synthesis.md` |
| Betroffene Repos | `ProjectHub/backend/`, `ProjectHub/frontend/`, (Konsument) `AI-Assist/` |
| Branch | `feat/research-auto-mode` (erstellt 2026-05-18 ab `test/synapse-e2e` nach Brain-WIP-Commit 1.4.0) |

> **Brain-Voraussetzung (v1.4.0)**: Diese Spec setzt voraus, dass der ProjectHub-Brain-Stack (P1+P2) gemerged ist — `services/embedding/` (T2.2), `services/retrieval/{contextual,enrichment,hybrid}.py` (T2.4-2.7), Memory-Bridge (P1), M1-Embedding-Spalten in `knowledge_items`. Auto-Mode konsumiert diesen Stack statt parallel zu implementieren. Brain-Reranker (T3.x) ist Premium-Pfad; falls noch nicht da, springt Auto-Mode-Eigen-Adapter (`bm25_llm`) ein.

---

## 1. Zusammenfassung

Die heutige Research-Funktion in ProjectHub (`backend/routers/knowledge.py:921-1039`) ist ein **blockierender Single-Shot-Call** gegen AI-Assist: ein Topic → ein LLM-Aufruf → ein einziges `KnowledgeItem`. Kein Streaming, keine Parallelität, keine Quellen-Validierung, keine semantische Zerlegung.

**Auto-Mode** ersetzt diesen Pfad durch eine **fünfphasige asynchrone Pipeline**, die parallel mehrere interne Quellen abfragt, Findings live an die UI streamt, jeden Claim validiert (Tier-B/C wie bei Synapsen) und am Ende optional eine inkrementelle Synapsen-Synthese auf die frisch entstandenen Items triggert.

Zusätzlich bekommt der Aufruf einen **Tiefen-Trigger** mit zwei Stufen — **Normal** und **Tief** — wobei "Tief" lateral expandiert ("links und rechts schauen"): Aus High-Confidence-Findings werden Entitäten extrahiert und für jede relevante Entität wird eine Folge-Sub-Query gespawnt, sodass bis zu zwei Such-Runden in einem Lauf durchlaufen werden.

---

## 2. Ziele und Nicht-Ziele

### 2.1 Ziele
- **Parallel-Suche** über mehrere interne Datenquellen mit Live-Stream an die UI.
- **Pluggable Provider-Architektur** mit gemeinsamem Adapter-Vertrag (`SearchProvider`).
- **Konfigurierbare Quellen** pro Projekt (enable/disable, per-Provider-Defaults).
- **Validierungs-Schicht** vor dem KB-Insert (Tier-B Grounding, optional Critic-Fan-out).
- **Tiefen-Steuerung**: Normal vs. Tief mit lateraler Entitäten-Expansion.
- **Wiederverwendung** der Synapsen-Validierungs-Infrastruktur (`synapse_validation.py` → `claim_aggregation.py`).
- **Keine Credentials in ProjectHub** — alle Auth bleibt zentral in AI-Assist.

### 2.2 Nicht-Ziele
- Keine eigene Web-Search-Implementierung (kommt von AI-Assist).
- Kein Schreiben in externe Systeme (Auto-Mode ist **read-only**).
- Kein Ersatz für die manuelle Synapsen-Generierung (die bleibt für Korpus-weites Re-Clustering).
- Keine Migration der existierenden Single-Shot-Research — sie bleibt als `mode=single` per Toggle erhalten.

---

## 3. Glossar

| Begriff | Bedeutung |
|---------|-----------|
| **Provider** | Adapter für eine konkrete Datenquelle (z.B. Confluence, Email). Implementiert `SearchProvider`. |
| **Sub-Query** | Eine vom Planner abgeleitete atomare Frage, die einer oder mehreren Providern zugewiesen wird. |
| **Finding** | Normalisiertes Suchergebnis eines Providers — Title, Snippet, Source-Ref, Konfidenz. |
| **Claim** | Aus einem Finding extrahierte atomare Aussage, die validiert werden kann. |
| **Run** | Eine Auto-Mode-Ausführung (ein DB-Row in `research_runs`). |
| **Tiefe** (Depth) | Steuert Breite/Tiefe der Suche: `normal` vs. `tief`. |
| **Lateral-Expansion** | "Links und rechts schauen" — Folge-Sub-Queries aus extrahierten Entitäten (nur in Tief-Mode). |
| **Hop** | Eine Runde lateraler Expansion. Tief-Mode erlaubt max. 2 Hops. |

---

## 4. Provider-Katalog (final, nach Review)

ServiceNow ist **entfernt** (User-Entscheidung 2026-05-16). 20 Provider in 4 Tiers:

### 4.1 Tier 1 — Lokal (ProjectHub-DB, keine externe Last)

| # | Key | Quelle | Latenz | Side-effect | Default an? |
|---|-----|--------|--------|-------------|-------------|
| 1 | `kb_fts` | KnowledgeItems FTS5 | Fast | read | **ja** |
| 2 | `project_documents` | docx/pdf-Scans | Fast | read | **ja** |
| 3 | `project_notes` | Projekt-Notizen | Fast | read | **ja** |
| 4 | `chat_history` | Frühere Chat-Sessions des Projekts | Fast | read | **ja** |

### 4.2 Tier 2 — Interne Systeme (via AI-Assist)

| # | Key | AI-Assist-Endpoint/Tool | Latenz | Default an? |
|---|-----|-------------------------|--------|-------------|
| 5 | `confluence` | `POST /api/research/confluence` | Slow | nein |
| 6 | `confluence_search` | Tool `search_confluence(query, limit, include_body)` | Med | nein |
| 7 | `email` | Tool `email_find(text, filter)` + `email_read` | Med | nein |
| 8 | `webex` | Tool `webex_search_all_rooms(text)` + `webex_messages` | Med | nein |
| 9 | `jira` | Tool `find_jira(text, filter)` | Med | nein |
| 10 | `log_servers` | Tool `log_grep`, `search_logs` | Med | nein |
| 11 | `handbook` | `POST /api/research/execute?sources=["handbook"]` | Fast | nein |
| 12 | `code_graph` | Tool `graph_search(query, type, language)` | Fast | nein |
| 13 | `github` | Tools `github_search_repos`, `github_list_prs`, `github_pr_details` | Med | nein |
| 14 | `jenkins` | Tools `jenkins_job_status`, `jenkins_build_info` (nur Read!) | Med | nein |
| 15 | `iq` | Tool `iq_findings(app_id, organization_id)` | Med | nein |
| 16 | `mq` | Tool `mq_list_queues` + per-Queue GET | Med | nein |

### 4.3 Tier 3 — Externe Quellen (opt-in, sanitization-pflichtig)

| # | Key | Endpoint/Tool | Latenz | Default an? |
|---|-----|---------------|--------|-------------|
| 17 | `web` | `POST /api/search/request` (DuckDuckGo, Confirmation-gated) | Med | nein |
| 18 | `mcp_context7` | MCP `mcp__context7__query-docs` | Med | nein |

### 4.4 Tier 4 — MCP-Auth-required (optional, Phase 12)

| # | Key | Tool | Status |
|---|-----|------|--------|
| 19 | `google_drive` | `mcp__claude_ai_Google_Drive__*` | Stub, Auth-Bridge Phase 12 |
| 20 | `gmail` | `mcp__claude_ai_Gmail__*` | Stub, Auth-Bridge Phase 12 |

**Blacklist** (hardcoded, niemals von Auto-Mode aufgerufen):
`iq_create_waiver`, `jenkins_trigger_build`, `email_send`, `webex_send`, `mq_publish`, alle MCP-Mutations.

---

## 5. Tiefen-Modi (Depth Trigger)

### 5.1 Übersicht

Der Auto-Mode kennt **zwei Tiefen-Stufen**, die per Request-Parameter `depth` gewählt werden:

| Aspekt | `normal` | `tief` |
|--------|----------|--------|
| **Use-Case** | Schnelle Recherche zu einem klar umrissenen Topic | "Umfassend recherchieren", inkl. verwandter Themen + Kontext |
| **Initiale Sub-Queries** | 3–5 | 6–8 |
| **Provider pro Sub-Query** | 1 (Planner wählt besten) | 1–3 (Planner wählt heterogen) |
| **Findings-Limit pro Provider** | 5 | 10 |
| **Lateral-Expansion** | aus | an (max 2 Hops) |
| **Entity-Extraktion** | nein | ja (pro High-Conf-Finding) |
| **Tier-B Grounding** | ja | ja |
| **Tier-C Critic-Fan-out** | nur bei Contradiction | **immer** |
| **Inline-Synapsen-Synthese** | optional (Setting) | **immer** (Setting überschreibt) |
| **Rerank-Strategie** | `bm25` (nur Lexical) | `bm25_llm` (BM25-Prefilter + LLM-Rerank) |
| **Token-Budget (soft / hard)** | 200 k / 400 k | 600 k / 1 000 k |
| **Auto-Degradation** | bei 80 % / 90 % / 95 % (siehe §5.6) | bei 70 % / 85 % / 95 % (siehe §5.6) |
| **Per-Run Hard-Timeout** | 180 s | 420 s |
| **UI-Indikator** | 🔍 | 🔍🔍 |

> **Hinweis**: Das frühere fixe `max_llm_calls`-Limit (12 / 30) ist durch ein **kategorie­basiertes Token-Budget mit Auto-Degradation** ersetzt — siehe §5.6. Die `rerank`-Kategorie ist vom Gesamt-Cap **ausgenommen** und skaliert eigenständig mit der Quellgröße.

### 5.2 Default-Verhalten

- `depth=normal` ist Default für den **Auto-Mode-Button** im UI.
- `depth=tief` wird über einen sekundären Toggle (Switch oder Dropdown) erreicht.
- Per Projekt konfigurierbar via `ProjectResearchSettings.default_depth`.

### 5.3 Tief-Mode: "Links und rechts schauen" — Lateral Expansion

Nach der initialen Such-Runde (Phase 2 + 3 — Search + Claim-Extract) läuft im Tief-Mode ein **Lateral-Planner** der aus den **Top-K Findings** (Konfidenz ≥ 0.6) Entitäten extrahiert und neue Sub-Queries spawnt.

**Algorithmus**:
```
INPUT: findings_round_1 (Top-K, K=8)
OUTPUT: lateral_subqueries (max 6)

1. entities = []
   for finding in findings_round_1:
       result = await extract_entities_from_finding(finding)
       #   reuse: services/synapse_entities.py extract logic
       #   limits: max 5 entities per finding
       entities.extend(result.entities)

2. seen_entities = {sq.entity_focus for sq in round_1_subqueries}
   new_entities = [e for e in entities if e.normalized not in seen_entities]
   new_entities = filter_high_value(new_entities)
   # filters: min length 3, not in blacklist (the, a, is, ...),
   #          frequency_score >= 2 (entity appears in ≥ 2 findings),
   #          OR confidence_score >= 0.8 (single high-conf mention)

3. ranked = rank_by_relevance(new_entities, original_topic)
   #   LLM call: "Which of these are most likely to deepen the
   #              understanding of {topic}? Score 0-1."

4. top_entities = ranked[:6]

5. lateral_subqueries = []
   for entity in top_entities:
       sq = await plan_lateral_subquery(
           original_topic=topic,
           entity=entity,
           context=relevant_findings,
       )
       #   LLM call: "Generate one sub-question that explores
       #              {entity} in the context of {topic}."
       sq.is_lateral = True
       sq.parent_finding_ids = entity.source_finding_ids
       sq.hop = 1
       lateral_subqueries.append(sq)

6. # Round 2 search executes lateral_subqueries
   findings_round_2 = await search_phase(lateral_subqueries)

7. if depth_settings.max_hops >= 2 and budget_remaining:
       # Optional: one more hop, with stricter filter (frequency_score >= 3)
       ...
```

**Beispiel-Lauf** (Topic: "OAuth2 PKCE in Service X"):
- Round 0 Sub-Queries: `[arch, code, incidents, communication]`
- Round 0 Findings (selection):
  - F1: "Service X uses keycloak-broker for token issuance" (Confluence, conf=0.91)
  - F2: "Marcus mentioned 90-day refresh-token policy" (Webex, conf=0.78)
  - F3: "PKCE was added in v4.2 commit a1b2c3d" (CodeGraph, conf=0.85)
- Extracted entities: `keycloak-broker`, `refresh-token policy`, `v4.2`, `Marcus`, `commit a1b2c3d`
- Ranked top-3 (after relevance scoring): `keycloak-broker`, `refresh-token policy`, `v4.2`
- Round 1 lateral sub-queries:
  - L1: "How is keycloak-broker configured for Service X?" → `[confluence, code_graph]`
  - L2: "What's the rationale for the 90-day refresh-token policy?" → `[email, webex, project_notes]`
  - L3: "What changed in Service X v4.2?" → `[github, jenkins, kb_fts]`
- Round 1 Findings: 7 new → validate → persist → synapse_run includes all 10+ items

### 5.4 Constraints für Tief-Mode

- **Max 2 Hops** (round 1 + round 2). Verhindert Runaway-Expansion.
- **Entity-Dedup** über alle Runden — eine Entität wird nie zweimal expandiert.
- **Budget-Check vor jedem Hop**: wenn `llm_calls_used ≥ 0.7 * max_llm_calls_per_run`, kein weiterer Hop.
- **Cancel-fähig** zwischen Hops (Run-Cancel beendet vor nächstem Hop).
- **Relevance-Cutoff**: lateral sub-queries mit `relevance_score < 0.5` werden verworfen — sonst floodet das den Run mit Off-Topic-Findings.

### 5.5 UI-Anzeige der Lateral-Expansion

Im Live-Stream wird die Eltern-Kind-Beziehung sichtbar gemacht:

```
Round 0 Sub-Queries:
  ✓ Welche Auth-Methoden nutzt Service X?
  ✓ PKCE-Code-Pfade in Service X?
  ✓ Gab es zuletzt Auth-Incidents?
  ✓ Welche Entscheidungen wurden im Auth-Channel getroffen?

🔄 Lateral-Hop 1 (3 neue Sub-Queries aus 5 Entitäten):
  ↪ keycloak-broker → "Wie ist keycloak-broker konfiguriert?"
  ↪ refresh-token policy → "Warum 90-Tage Policy?"
  ↪ v4.2 → "Was änderte sich in v4.2?"
```

Im `FindingDetail`-View zeigt ein Breadcrumb: `Round 0 (arch) → keycloak-broker → Round 1 (config)`.

---

### 5.6 Hybrid-Rerank-Strategie (Retrieve → Rerank → Summarize) — **Brain-aware**

> **Architektur-Update 2026-05-18**: ProjectHub hat ab v1.4.0 einen **Brain-Stack** (`services/embedding/`, `services/retrieval/hybrid.py`, `services/retrieval/contextual.py`, geplant `services/retrieval/reranker.py`). Auto-Mode konsumiert diesen Stack statt parallel zu implementieren. Die folgenden Stage-Modi sind das Resultat dieser Konsolidierung.

Lange Quellen (Confluence-Spaces, PDFs, lange Email-Threads, Code-Graph-Hits) erzeugen unkontrolliert viele Roh-Chunks. Reine lexikalische Filter (BM25/FTS) sind blitzschnell aber semantik-blind; reines LLM-Ranking ist semantisch stark aber pro-Chunk teuer. Embedding-Cosine ist semantisch + deterministisch + billig — aber braucht einen Embedder. Auto-Mode wählt zur Laufzeit zwischen vier Strategien — je nachdem welche Brain-Komponenten aktiviert sind:

```
                       N Roh-Chunks
                            │
                            ▼
        ┌───────────── Stage 1: BM25/FTS-Prefilter ─────────────┐
        │   billig (< 5 ms je Provider, KEIN LLM)               │
        │   dropt ~60-80 % offensichtlich irrelevantes          │
        │   konfigurierbar: bm25_top_n (default 15 für Tief)    │
        └─────────────────────────┬─────────────────────────────┘
                                  ▼
                          N' Survivor-Chunks
                                  │
                       ┌──────────┴──────────┐
                       │  rerank.mode? +     │
                       │  Brain-Flags-Check  │
                       └──┬───┬───┬───┬──────┘
                          │   │   │   │
       ┌──────────────────┘   │   │   └──────────────────┐
       │                      │   │                       │
   "none"/"bm25"      "bm25_embedding"   "bm25_brain"   "bm25_llm"
       │            (Embedder cosine)   (Brain-Reranker (LLM-Rerank
       │                  │              Adapter)        Fallback)
       │                  │                  │                  │
       ▼                  ▼                  ▼                  ▼
   skip Stage 2,   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
   nimm Top-K      │ Stage 2a:      │  │ Stage 2b:      │  │ Stage 2c:      │
   direkt          │ Embed Query +  │  │ Brain-Reranker │  │ LLM-Rerank     │
       │           │ Embed Chunks   │  │ (T3.x, geplant │  │ (BATCH!)       │
       │           │ (Batch via     │  │  in services/  │  │ 1 Call pro     │
       │           │  /api/embed)   │  │  retrieval/    │  │ Batch von N'   │
       │           │ → cosine sort  │  │  reranker.py)  │  │ ein Prompt →   │
       │           │ KAT "embedding"│  │ KAT "rerank"   │  │ JSON-Scores    │
       │           └───────┬────────┘  └───────┬────────┘  │ KAT "rerank"   │
       │                   │                   │           └───────┬────────┘
       │                   │                   │                   │
       │                   └─────────┬─────────┘───────────────────┘
       │                             │
       │             K Top-Chunks nach Score (Stage 2 vereint)
       └──────────────┬──────────────┘
                      ▼
       ┌────────────────────────────────────────────────────────┐
       │ Stage 3: MINI-SUMMARY pro Top-Chunk                    │
       │   1 LLM-Call pro Chunk (parallel, Semaphor 4)          │
       │   ~3.3 k Token / Chunk                                 │
       │   → KATEGORIE "summary" — gegen Budget verrechnet      │
       └────────────────────────┬───────────────────────────────┘
                                ▼
                          K Findings (~200 T jeweils)
                          → an Orchestrator
```

**Effektivität in Zahlen** (50 Confluence-Pages, Sub-Query semantisch unscharf):

| Strategie | Stage-2-Calls | Token-Aufwand | Recall (geschätzt) | Latenz | Im Tief-Budget? |
|-----------|---------------|---------------|--------------------|--------|-----------------|
| `none` | 0 | 0 | — | < 1 s | — |
| `bm25` (Normal-Default) | 0 Rerank + 5 Summary | ~16 k | ~75 % | ~3 s | ja |
| `bm25_embedding` (**Tief-Default, Brain ≥ v1.4**) | 1 embed-batch + 5 Summary | ~18 k | ~88 % | ~3 s | ja |
| `bm25_brain` (Brain-Reranker an, T3.x) | 1 Brain-Reranker + 5 Summary | ~20 k | ~92 % | ~4 s | ja |
| `bm25_llm` (LLM-Rerank, Fallback) | 1 LLM-Rerank-Batch + 5 Summary | ~19 k | ~90 % | ~4 s | ja |
| `llm_only` (opt-in) | 50 + 5 = 55 | ~190 k | ~95 % | ~20 s | grenzwertig |

**Strategie-Wahl zur Laufzeit (`RerankStrategy.mode="auto"`)**:
```
if brain_reranker_enabled and reranker_available:
    use bm25_brain      # T3.x ist Premium
elif brain_embedding_enabled and embedder_healthy:
    use bm25_embedding  # deterministisch, billig, parallel
elif settings.research.allow_llm_rerank_fallback:
    use bm25_llm        # Eigenes LLM-Batch-Rerank
else:
    use bm25            # Lexikalisch only
```

**Provider-Spezifika**:
- **Lokale Tier-1-Provider** (`kb_fts`, `project_documents`, `project_notes`, `chat_history`): nutzen direkt `services/retrieval/hybrid.py` (RRF FTS5 + cosine, sofern `brain_embedding_enabled`). Keine zusätzliche Auto-Mode-Rerank-Stufe nötig — Brain liefert bereits scored Top-K.
- **AI-Assist `/api/research/confluence`** (Confluence-Deep): liefert bereits **synthetisiertes Markdown + findings[]** — keine Rerank-Stufe.
- **Andere Tier-2** (`confluence_search`, `email`, `webex`, `jira`, `log_servers`, `code_graph`, `github`): folgen dem 3-Stage-Pattern mit `mode="auto"`.
- **Externe** (`web`): immer `bm25` Default (Output ist klein), `llm_only` opt-in.

**Wichtig — Embedding-Pre-Computation**: Damit `bm25_embedding` greift, müssen die Chunks der Quelle entweder
1. **bereits embedded sein** (KnowledgeItems mit `embedded_at != NULL` aus Brain-T2.6-Backfill), oder
2. **zur Laufzeit embedded werden** (1 batch-Call an `/api/embed` für die N' Survivor-Chunks).

Für ad-hoc-Quellen (Confluence-Snippets, Email-Bodies) ist Variante 2 der Default. Latenz: ein Embedder-Call ~500 ms für 15 Chunks.

**Batch-Prompting (Stage 2 Schlüsseltrick):**
```
LLM-Input (1 Call für 15 Chunks):
   "Topic: <Sub-Query>
    Bewerte für jeden Chunk die Relevanz zum Topic auf 0-1.
    Antworte als JSON-Array [{chunk_id, score, reason_short}].
    
    Chunks:
    [1] <title>: <300-char snippet>
    [2] <title>: <300-char snippet>
    ... [15] ..."

LLM-Output: ~200 T (15× compact scores)
Token-Kost: ~3 k Input + 200 Output ≈ 3.2 k pro Batch
```

Statt 15 separater Calls (15 × 3 k = 45 k Token + 15× Latenz) ein einziger Batch-Call. Bei > 15 Chunks → mehrere Batches sequenziell.

### 5.7 Budget-Modell und Auto-Degradation

Statt eines starren `max_llm_calls`-Limits arbeitet Auto-Mode mit einem **dynamischen, kategoriebasierten Token-Budget**, das in vier Aspekten von der naiven Variante abweicht:

1. **Token-basiert, nicht Call-basiert**: ein 7 k-Token-Critic-Call kostet anders als ein 200-Token-Rerank-Call.
2. **Kategorisiert**: pro Call-Typ ein eigener Sub-Counter. Rerank ist exempt.
3. **Soft + Hard Cap**: ab Soft-Cap wird **automatisch degradiert** (siehe Ladder); Hard-Cap = Abbruch-Schutz.
4. **Pro Call reservierbar**: jeder Call fragt erst Budget an. Bei Mangel → Caller bekommt **degradation hint** statt Exception.

#### Kategorien & Kosten-Schätzungen

| Kategorie | Typische Tokens/Call | Bemerkung |
|-----------|---------------------|-----------|
| `planning` | ~2.5 k (2 k in + 500 out) | wenige, prio-1 |
| `embedding` | ~3 k (input only, 1 batch ≤ 64 chunks) | **EXEMPT**. Brain-Embedder via `/api/embed`. Output ist Vektoren, kein Generation-Cost. |
| `rerank` | ~3.2 k (3 k in + 200 out) — LLM-Pfad / ~0 — Embedding-Pfad / ~0 — Brain-Reranker | **EXEMPT**. Konkrete Kosten je nach gewählter Stage-2-Strategie. |
| `summary` | ~3.3 k (3 k in + 300 out) | wächst mit Survivor-Anzahl |
| `entity_extract` | ~1.3 k (1 k in + 300 out) | nur Tief |
| `lateral_plan` | ~1.5 k (1 k in + 500 out) | nur Tief |
| `grounding` | ~2.2 k (2 k in + 200 out) | pro Claim |
| `critic` | ~7.5 k (~ 3 × 2.5 k Sub-Calls) | Tief: alle; Normal: nur Contradiction |
| `synthesis` | ~4 k (3 k in + 1 k out) | optional |

#### Rerank-Sonderregel ("ausgesetzt")

Rerank-Calls sind **strukturell notwendig**, um Token-Explosionen in den Folge-Phasen zu verhindern. Sie werden **vom Gesamt-Cap ausgenommen**, aber durch eine eigene Schutzregel begrenzt:

```python
rerank_self_limit = min(
    chunks_seen * RERANK_PER_CHUNK_EST,  # natürliche Obergrenze
    profile.rerank.batch_size * RERANK_PER_CHUNK_EST * MAX_BATCHES,
)
# MAX_BATCHES = 10 → Schutz gegen pathologisch große Quellen
```

→ Ein Provider mit 1 000 Chunks darf nicht 1 000 Rerank-Tokens verbraten; Cap = 10 Batches × 15 Chunks = 150 Chunks maximal in den Rerank, der Rest wird **vom Provider-internen BM25 abgeschnitten**, bevor er die Rerank-Pipeline überhaupt erreicht.

#### Auto-Degradation-Ladder

Wenn `total_tokens_used / hard_cap_tokens` über Schwellen klettert, schaltet die Pipeline **automatisch defensivere Strategien**:

| Druck-Stufe | Schwelle Normal | Schwelle Tief | Maßnahme |
|-------------|-----------------|---------------|----------|
| `ok` | < 80 % | < 70 % | normal |
| `warn` | 80 % | 70 % | Log-Warning, SSE-Event `research_budget`, **keine Verhaltensänderung** |
| `tight` | 85 % | 80 % | Rerank-K halbieren (10 → 5), Mini-Summary maxlen 300 → 200 |
| `critical` | 90 % | 85 % | Tier-C **droppen** → Tier-B-only für noch ausstehende Findings; flagged-Confidence-Band fällt zurück auf `medium` |
| `extreme` | 95 % | 95 % | weitere Lateral-Hops **skippen**, Inline-Synthesis **skippen** |
| `exhausted` | 100 % | 100 % | aktuelle Sub-Query finalisieren, restliche Sub-Queries **skippen** → `status=partial`, persistierte Findings bleiben |

**Wichtig**: Die Pipeline **bricht nie hart ab**. Auch bei `exhausted` werden die bereits validierten + persistierten Findings beibehalten — der Run endet mit `status=partial` statt `error`.

#### Token-Reservation-Pattern

```python
async def _llm_call_with_budget(
    category: Literal["planning","rerank","summary","entity_extract",
                       "lateral_plan","grounding","critic","synthesis"],
    est_in: int,
    est_out: int,
    call_fn,
    *args, **kwargs,
) -> LLMResult:
    est_total = est_in + est_out
    reservation = await budget.reserve(category, est_total)
    
    if not reservation.allow:
        # Caller bekommt einen Hinweis statt Exception:
        raise BudgetDegradation(reservation.suggested_action)
        # Caller-Code reagiert (z.B. ohne Critic weitermachen, Synthesis skippen, ...)
    
    result = await call_fn(*args, **kwargs)
    actual = result.usage.total_tokens
    await budget.commit(category, actual)
    
    # SSE-Event nur bei Schwellen-Übergang:
    new_level = budget.pressure_level()
    if new_level != reservation.pre_level:
        await sse_hub.emit("research_budget", {
            "run_id": run_id, "level": new_level, "used": budget.total,
            "hard_cap": budget.hard_cap_tokens, "by_category": budget.snapshot(),
        })
    return result
```

#### Adaptive Budget-Erweiterung

Wenn der Planner zur Laufzeit erkennt, dass eine Sub-Query eine **außergewöhnlich große Quelle** treffen wird (z.B. Confluence-Deep für einen ganzen Space), darf er **einmalig** das Budget anpassen:

```python
# Im Planner-Output (optional pro Sub-Query):
{
  "id": "sq3",
  "providers": ["confluence"],
  "expected_cost": "heavy",   # "light" | "medium" | "heavy"
  "budget_request": 150_000,   # zusätzliche Tokens für diese SQ
}
```

Budget-Tracker akzeptiert max **1 Erweiterung pro Run**, max **+30 %** vom hard_cap. Wird transparent ge-loggt + per SSE gemeldet. Verhindert das Szenario „Plan kennt Quellgröße, Budget ist statisch zu klein". 

#### Pro Tiefe vorkonfiguriert

```python
profiles = {
    "normal": ResearchDepthProfile(
        # ... (siehe §8.1)
        rerank=RerankStrategy(mode="bm25", bm25_top_n=10, llm_rerank_top_k=5, batch_size=15),
        budget=TokenBudgetPolicy(
            soft_cap_tokens=200_000,
            hard_cap_tokens=400_000,
            per_category_caps={
                "planning":     20_000,
                "summary":     150_000,
                "grounding":   100_000,
                "critic":       40_000,   # Normal: selten genutzt
                "synthesis":     0,       # Normal: aus
            },
            exempt_categories=["rerank"],
        ),
    ),
    "tief": ResearchDepthProfile(
        # ... (siehe §8.1)
        rerank=RerankStrategy(mode="bm25_llm", bm25_top_n=15, llm_rerank_top_k=8, batch_size=15),
        budget=TokenBudgetPolicy(
            soft_cap_tokens=600_000,
            hard_cap_tokens=1_000_000,
            per_category_caps={
                "planning":      30_000,
                "summary":      400_000,
                "entity_extract":80_000,
                "lateral_plan":  20_000,
                "grounding":    200_000,
                "critic":       400_000,  # Tief: hauptkost — first to degrade
                "synthesis":     30_000,
            },
            exempt_categories=["rerank"],
        ),
    ),
}
```

#### Beobachtbarkeit

Pro Run wird in `ResearchRun.token_usage` (JSON) persistiert:
```json
{
  "by_category": {
    "planning": 4200,
    "rerank": 18400,
    "summary": 138000,
    "entity_extract": 32000,
    "lateral_plan": 8500,
    "grounding": 92000,
    "critic": 215000,
    "synthesis": 12000
  },
  "total": 520100,
  "soft_cap": 600000,
  "hard_cap": 1000000,
  "max_pressure_reached": "warn",
  "degradations_triggered": []
}
```

UI zeigt im `FindingDetail` und Run-Summary einen Budget-Bar (grün/gelb/rot) + Tooltip mit Kategorie-Breakdown.

---

## 6. Pipeline-Architektur

### 6.1 Phasen

```
                       POST /api/research/{pid}/runs
                       { topic, depth: "normal"|"tief", providers_override? }
                               │
                               ▼
                        ┌─────────────────┐
                        │ Phase 1: PLAN   │  Planner zerlegt Topic in
                        │ (Planner-LLM)   │  N Sub-Queries + Provider-Routing
                        └─────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌────────────┐   ┌────────────┐   ┌────────────┐
       │  Phase 2:  │   │  Phase 2:  │   │  Phase 2:  │   ◄── SEARCH-Fan-out
       │ Provider#1 │   │ Provider#2 │   │ Provider#N │       (parallel,
       │            │   │            │   │            │        Semaphor 4)
       │ ┌────────┐ │   │ ┌────────┐ │   │ ┌────────┐ │
       │ │ BM25-  │ │   │ │ BM25-  │ │   │ │ BM25-  │ │   ◄── Stage 1 (intern)
       │ │Filter  │ │   │ │Filter  │ │   │ │Filter  │ │
       │ └───┬────┘ │   │ └───┬────┘ │   │ └───┬────┘ │
       │     ▼      │   │     ▼      │   │     ▼      │
       │ ┌────────┐ │   │ ┌────────┐ │   │ ┌────────┐ │
       │ │ LLM-   │ │   │ │ LLM-   │ │   │ │ LLM-   │ │   ◄── Stage 2 (Tief)
       │ │Rerank* │ │   │ │Rerank* │ │   │ │Rerank* │ │       *exempt
       │ └───┬────┘ │   │ └───┬────┘ │   │ └───┬────┘ │
       │     ▼      │   │     ▼      │   │     ▼      │
       │ ┌────────┐ │   │ ┌────────┐ │   │ ┌────────┐ │
       │ │ Mini-  │ │   │ │ Mini-  │ │   │ │ Mini-  │ │   ◄── Stage 3
       │ │Summary │ │   │ │Summary │ │   │ │Summary │ │
       │ └────────┘ │   │ └────────┘ │   │ └────────┘ │
       └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
              │                │                │
              └────────────────┼────────────────┘
                               ▼  (kompakte Findings, je ~200 T)
                        ┌─────────────────┐
                        │ Phase 3:        │  Pro Finding: atomare Claims
                        │ EXTRACT         │  + Quellen-Refs
                        └─────────────────┘
                               │
                  ┌────────────┴────────────┐
                  │  depth == "tief"?       │
                  └────┬──────────────┬─────┘
                       │              │
                  yes  │              │  no
                       ▼              ▼
              ┌─────────────────┐    │
              │ Phase 3b:       │    │   ◄── nur Tief-Mode
              │ LATERAL EXPAND  │    │
              │ (max 2 Hops)    │    │
              └─────────────────┘    │
                       │              │
                       └──────┬───────┘
                              ▼
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌────────────┐   ┌────────────┐   ┌────────────┐
       │  Phase 4:  │   │  Phase 4:  │   │  Phase 4:  │   ◄── VALIDATE
       │ Tier-B/C   │   │ Tier-B/C   │   │ Tier-B/C   │       (per-claim)
       └────────────┘   └────────────┘   └────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │ Phase 5:        │  Persist KnowledgeItems
                        │ PERSIST + LINK  │  + Edges (Quellen + Tags)
                        └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │ Phase 6:        │  Inline Synapse-Run
                        │ SYNTHESISE      │  (auf neue Items beschränkt)
                        │ (optional)      │
                        └─────────────────┘

  Live-SSE über alle Phasen: research_progress, research_finding,
                              research_subquery_started,
                              research_finding_updated, research_complete
```

### 6.2 Komponenten-Inventar

| Komponente | Pfad | Status | Vorbild |
|------------|------|--------|---------|
| `SearchProvider`-Protocol | `backend/services/research_providers/base.py` | neu | — |
| Tier-1-Adapter (lokal) | `research_providers/{kb_fts,project_documents,project_notes,chat_history}.py` | neu | — |
| Tier-2-Adapter (intern) | `research_providers/{confluence,confluence_search,email,webex,jira,log_servers,handbook,code_graph,github,jenkins,iq,mq}.py` | neu | — |
| Tier-3-Adapter (extern) | `research_providers/{web,mcp_context7}.py` | neu | — |
| Streaming-Helper | `research_providers/_streaming.py` | neu | `ai_assist_client.py:180-272` |
| **BM25-Prefilter** | `research_providers/_bm25.py` | neu (klein) | rank-bm25 lib oder Eigen-Impl |
| **Rerank-Adapter (Multi-Strategy)** | `services/research_rerank.py` | neu — **Adapter** mit Strategien `embedding\|brain\|llm\|none`; konsumiert `services/embedding/litellm_router.py` + (sobald da) `services/retrieval/reranker.py` aus Brain | siehe §5.6 |
| **Brain-Retrieval-Konsument** | (in `kb_fts.py` + `project_documents.py`) | nutzt `services/retrieval/hybrid.py` direkt | Brain T2.7 |
| **BudgetTracker** | `services/research_budget.py` | neu | Async-safe Counter mit Reservation/Commit |
| Health-Service | `services/research_health.py` | neu | — |
| Planner | `services/research_planner.py` | neu | `synapse_pipeline.py` Phase-Pattern |
| Lateral-Planner | `services/research_lateral.py` | neu (Tief-only) | — |
| Pipeline-Orchestrator | `services/research_pipeline.py` | neu | `synapse_pipeline.py:77-186` |
| Validation (geteilt) | `services/claim_aggregation.py` | neu (Extract aus Synapse) | `synapse_validation.py:25-300` |
| Router (Runs) | `routers/research.py` | neu | `routers/synapse.py` |
| Router (Settings) | `routers/project_research_settings.py` | neu | — |
| Models | `models/research.py` | neu | `models/synapse.py:264-302` |
| Store | `frontend/src/stores/researchStore.ts` | neu | `stores/synapseStore.ts:23-44` |
| UI: Auto-Bar | `components/research/ResearchAutoBar.tsx` | neu | `SynapseGenerateBar.tsx:36-68` |
| UI: Sub-Query-Strip | `components/research/SubQueryStrip.tsx` | neu | — |
| UI: Findings-Stream | `components/research/FindingsStream.tsx` | neu | — |
| UI: Finding-Detail | `components/research/FindingDetail.tsx` | neu | `SynapseCard.tsx` |
| UI: Source-Settings | `components/settings/SourcesPanel.tsx` | neu | — |
| Refactor: ResearchDialog | `components/knowledge/ResearchDialog.tsx` | erweitern (Mode + Depth) | bestehend |

### 6.3 Konkurrenz-Modell

- **Pro Projekt**: max. **1 aktiver Run** (analog `routers/synapse.py:212-220`). Zweiter Aufruf erhält `already_running` mit Run-ID.
- **Pro Run**: globaler Semaphor `settings.research.max_concurrent_searches` (default 4).
- **Cancel**: `asyncio.Event` in jedem Run-State; an alle Provider via `cancel`-Param durchgereicht. Zwischen Hops geprüft.

---

## 7. Datenmodell

### 7.1 Neue Tabellen

```python
# models/research.py

class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str]
    project_id: Mapped[str]
    topic: Mapped[str]
    depth: Mapped[str]           # "normal" | "tief"
    mode: Mapped[str]            # "auto" | "single" (legacy)
    status: Mapped[str]          # running | ok | partial | error | cancelled
    phase: Mapped[str]           # planning | searching | extracting | lateral | validating | persisting | synthesising | done
    current_hop: Mapped[int]     # 0 = initial, 1+ = lateral
    sub_query_count: Mapped[int]
    finding_count: Mapped[int]
    validated_count: Mapped[int]
    persisted_count: Mapped[int]
    flagged_count: Mapped[int]
    rejected_count: Mapped[int]
    synapse_run_id: Mapped[str | None]
    llm_calls_used: Mapped[int]
    token_usage: Mapped[str]     # JSON
    error_summary: Mapped[str | None]
    started_at: Mapped[str]
    finished_at: Mapped[str | None]

class ResearchSubQuery(Base):
    __tablename__ = "research_sub_queries"
    id: Mapped[str]
    run_id: Mapped[str]          # FK research_runs.id
    hop: Mapped[int]             # 0 = initial, 1+ = lateral
    parent_finding_ids: Mapped[str]  # JSON (lateral parents)
    question: Mapped[str]
    providers: Mapped[str]       # JSON array of provider keys
    rationale: Mapped[str]
    priority: Mapped[int]
    relevance_score: Mapped[float | None]  # only for lateral
    entity_focus: Mapped[str | None]       # only for lateral
    is_lateral: Mapped[bool]
    status: Mapped[str]          # pending | running | done | failed | cancelled
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]

class ResearchFinding(Base):
    __tablename__ = "research_findings"
    id: Mapped[str]
    run_id: Mapped[str]
    sub_query_id: Mapped[str]
    provider_key: Mapped[str]
    source_ref: Mapped[str]      # e.g. "confluence:page-456"
    title: Mapped[str]
    snippet: Mapped[str]
    full_content: Mapped[str | None]
    url: Mapped[str | None]
    timestamp: Mapped[str | None]
    author: Mapped[str | None]
    raw_metadata: Mapped[str]    # JSON
    status: Mapped[str]          # candidate | grounded | flagged | rejected | persisted | failed
    confidence: Mapped[float | None]
    knowledge_item_id: Mapped[str | None]  # FK knowledge_items.id (after persist)
    extra_data: Mapped[str]      # JSON (claim breakdown, validation verdicts)
    created_at: Mapped[str]
    updated_at: Mapped[str]

class ProjectResearchSettings(Base):
    __tablename__ = "project_research_settings"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    default_depth: Mapped[str]   # "normal" | "tief"
    enabled_providers: Mapped[str]  # JSON array of provider keys
    provider_settings: Mapped[str]  # JSON: per-key overrides
    routing_hints: Mapped[str]      # Free-text for Planner
    updated_at: Mapped[str]
```

### 7.2 Knowledge-Integration

Persistierte Findings landen als `KnowledgeItem`:
```python
KnowledgeItem(
    title=f"Recherche: {topic} — {finding.title}",
    source_type="research_auto",
    source_ref=f"research:{run_id}:{finding.id}",  # idempotent
    confidence={"high":"high","medium":"medium","low":"low"}[band],
    extra_data=json.dumps({
        "run_id": run_id,
        "sub_query_id": sq.id,
        "provider": provider_key,
        "hop": hop,
        "parent_finding_ids": [...],  # lateral lineage
        "validation": {"tier_b":..., "tier_c":...},
        "claims": [...],
    }),
)
```

Edges:
- **Auto-Edge** zwischen Finding-Items, wenn `parent_finding_ids` gesetzt (`edge_type="derived_from"`).
- **Tag-Edge** via bestehendes `_auto_link_by_tags()` (`knowledge.py:1516-1541`).

---

## 8. Config-Schema

### 8.1 Backend (`backend/config.py`)

```python
class ResearchProviderDefaults(BaseModel):
    enabled: bool = False
    max_results: int = 8
    timeout_sec: int = 60

class ResearchProviderRegistry(BaseModel):
    # Tier 1 (defaults on)
    kb_fts: ResearchProviderDefaults = ResearchProviderDefaults(enabled=True)
    project_documents: ResearchProviderDefaults = ResearchProviderDefaults(enabled=True)
    project_notes: ResearchProviderDefaults = ResearchProviderDefaults(enabled=True)
    chat_history: ResearchProviderDefaults = ResearchProviderDefaults(enabled=True)
    # Tier 2 (defaults off)
    confluence: ResearchProviderDefaults = ResearchProviderDefaults()
    confluence_search: ResearchProviderDefaults = ResearchProviderDefaults()
    email: ResearchProviderDefaults = ResearchProviderDefaults()
    webex: ResearchProviderDefaults = ResearchProviderDefaults()
    jira: ResearchProviderDefaults = ResearchProviderDefaults()
    log_servers: ResearchProviderDefaults = ResearchProviderDefaults()
    handbook: ResearchProviderDefaults = ResearchProviderDefaults()
    code_graph: ResearchProviderDefaults = ResearchProviderDefaults()
    github: ResearchProviderDefaults = ResearchProviderDefaults()
    jenkins: ResearchProviderDefaults = ResearchProviderDefaults()
    iq: ResearchProviderDefaults = ResearchProviderDefaults()
    mq: ResearchProviderDefaults = ResearchProviderDefaults()
    # Tier 3 (opt-in)
    web: ResearchProviderDefaults = ResearchProviderDefaults()
    mcp_context7: ResearchProviderDefaults = ResearchProviderDefaults()
    # Tier 4 (Phase 12)
    google_drive: ResearchProviderDefaults = ResearchProviderDefaults()
    gmail: ResearchProviderDefaults = ResearchProviderDefaults()

class RerankStrategy(BaseModel):
    """Wie ein Provider seine Roh-Treffer auf Findings reduziert.

    `mode="auto"` wählt zur Laufzeit:
       brain_reranker_enabled + reranker_available → "bm25_brain"
       brain_embedding_enabled + embedder_healthy → "bm25_embedding"
       allow_llm_rerank_fallback                  → "bm25_llm"
       sonst                                      → "bm25"
    """
    mode: Literal["auto","none","bm25","bm25_embedding","bm25_brain","bm25_llm","llm_only"] = "auto"
    bm25_top_n: int = 15             # Survivors nach Stage-1
    rerank_top_k: int = 8            # Top-K nach Stage-2 → in Summary
    batch_size: int = 15             # max Chunks pro Batch (LLM oder Embedding)
    max_batches: int = 10            # Schutz gegen pathologisch große Quellen
    rerank_per_chunk_est_tokens: int = 3500  # Self-Limit-Schätzung (LLM-Pfad)
    embedding_per_batch_est_tokens: int = 3000  # Self-Limit-Schätzung (Embedding-Pfad)
    allow_llm_rerank_fallback: bool = True  # wenn Brain offline, darf LLM-Rerank ran?

class TokenBudgetPolicy(BaseModel):
    """Kategoriebasiertes Budget mit Auto-Degradation."""
    soft_cap_tokens: int
    hard_cap_tokens: int
    per_category_caps: dict[str, int]    # Kategorie → Token-Limit
    exempt_categories: list[str] = ["rerank"]  # Rerank ist self-limiting
    # Adaptive Erweiterung: Planner darf einmalig +30% beantragen
    max_adaptive_extensions: int = 1
    max_extension_fraction: float = 0.30

class ResearchDepthProfile(BaseModel):
    """Per-Tiefe Verhalten + Rerank-Strategie + Token-Budget."""
    max_initial_sub_queries: int
    max_providers_per_sub_query: int
    max_findings_per_provider: int
    max_lateral_hops: int             # 0 = no lateral
    max_lateral_sub_queries: int      # per hop
    relevance_cutoff: float
    enable_critic_fanout: bool
    auto_synthesise: bool
    rerank: RerankStrategy
    budget: TokenBudgetPolicy
    hard_timeout_sec: int

class ResearchSettings(BaseModel):
    providers: ResearchProviderRegistry = ResearchProviderRegistry()
    max_concurrent_searches: int = 4
    max_providers_per_run: int = 6
    planner_model: str = ""
    critic_models: list[str] = []
    web_auto_approve: bool = False

    profiles: dict[str, ResearchDepthProfile] = {
        "normal": ResearchDepthProfile(
            max_initial_sub_queries=5,
            max_providers_per_sub_query=1,
            max_findings_per_provider=5,
            max_lateral_hops=0,
            max_lateral_sub_queries=0,
            relevance_cutoff=0.6,
            enable_critic_fanout=False,
            auto_synthesise=False,
            rerank=RerankStrategy(
                mode="bm25",  # Normal bleibt simpel-schnell
                bm25_top_n=10,
                rerank_top_k=5,
                batch_size=15,
            ),
            budget=TokenBudgetPolicy(
                soft_cap_tokens=200_000,
                hard_cap_tokens=400_000,
                per_category_caps={
                    "planning":      20_000,
                    "summary":      150_000,
                    "grounding":    100_000,
                    "critic":        40_000,   # Normal: nur Contradiction-Pfad
                    "synthesis":          0,   # Normal: aus
                },
                exempt_categories=["rerank", "embedding"],
            ),
            hard_timeout_sec=180,
        ),
        "tief": ResearchDepthProfile(
            max_initial_sub_queries=8,
            max_providers_per_sub_query=3,
            max_findings_per_provider=10,
            max_lateral_hops=2,
            max_lateral_sub_queries=6,
            relevance_cutoff=0.5,
            enable_critic_fanout=True,
            auto_synthesise=True,
            rerank=RerankStrategy(
                mode="auto",  # Tief: best available — Brain-Reranker > Embedding > LLM > BM25
                bm25_top_n=15,
                rerank_top_k=8,
                batch_size=15,
            ),
            budget=TokenBudgetPolicy(
                soft_cap_tokens=600_000,
                hard_cap_tokens=1_000_000,
                per_category_caps={
                    "planning":       30_000,
                    "summary":       400_000,
                    "entity_extract": 80_000,
                    "lateral_plan":   20_000,
                    "grounding":     200_000,
                    "critic":        400_000,  # Tief: Haupt-Kostenstelle; first to degrade
                    "synthesis":      30_000,
                },
                exempt_categories=["rerank", "embedding"],
            ),
            hard_timeout_sec=420,
        ),
    }
```

> **Migrationshinweis**: Frühere Spec-Version nutzte `max_llm_calls: int`. Dies ist **ersetzt** durch das `TokenBudgetPolicy`-Modell. Es gibt keinen einzelnen „Call-Cap" mehr — Budget wird in Tokens pro Kategorie gerechnet, mit Auto-Degradation statt hartem Crash. Siehe §5.7 für die Ladder.

### 8.2 Pro-Projekt (Override via `ProjectResearchSettings`)

```json
{
  "default_depth": "normal",
  "enabled_providers": ["kb_fts","project_documents","confluence","email","webex","jira","code_graph"],
  "provider_settings": {
    "confluence": {"spaces": ["TEAM","ARCH"], "max_pages": 15},
    "email": {"days_back": 30, "max_results": 10},
    "webex": {"rooms": ["abc","xyz"], "days_back": 14},
    "jira": {"default_project": "PROJ", "statuses": ["open","inprogress"]},
    "code_graph": {"language": "java"},
    "iq": {"app_id": "my-app-id"}
  },
  "routing_hints": "Bei Auth-Themen immer Confluence vor Code-Graph."
}
```

---

## 9. API-Surface

### 9.1 Run-Routes

```
POST   /api/research/{pid}/runs
       Body:
         {
           "topic": "string",                       # required
           "depth": "normal" | "tief",              # default: project setting
           "mode": "auto" | "single",               # default: "auto"
           "providers_override": ["kb_fts", ...],   # optional, subset of enabled
           "sub_queries_override": [...],           # optional, skip planner
           "max_llm_calls_override": int            # optional, capped by profile
         }
       Response (202):
         { "run_id": "...", "started": true, "depth": "normal" }
       Response (409):
         { "run_id": "<existing>", "started": false, "reason": "already_running" }

GET    /api/research/{pid}/runs?limit=20&status=running,ok
       Response: [{ id, topic, depth, status, phase, started_at, finished_at, counts }]

GET    /api/research/runs/{run_id}
       Response:
         {
           "run": {...},
           "sub_queries": [{...}],
           "findings": [{...}],
           "validation_summary": {...}
         }

POST   /api/research/runs/{run_id}/cancel
       Response: { "cancelled": true | false }

POST   /api/research/runs/{run_id}/findings/{fid}/accept
POST   /api/research/runs/{run_id}/findings/{fid}/reject
       Body: { "note": "optional reason" }
       Response: { "ok": true }
```

### 9.2 Provider-Routes

```
GET    /api/research/{pid}/providers
       Response: [{ key, enabled, default_enabled, description,
                    typical_latency, side_effect, settings, health }]

GET    /api/research/{pid}/providers/health?refresh=true
       Response: { kb_fts: {ok, detail, last_checked_at}, ... }

GET    /api/research/{pid}/settings
PUT    /api/research/{pid}/settings
       Body: ProjectResearchSettings payload
       Response: { "ok": true, "settings": {...} }
```

### 9.3 SSE-Events

| Event | Wann | Payload |
|-------|------|---------|
| `research_progress` | Jeder Phasen-Übergang + Heartbeat 5s | `{project_id, run_id, phase, hop, current?, total?}` |
| `research_subquery_started` | Pro Sub-Query | `{project_id, run_id, sub_query_id, hop, providers, is_lateral, parent_finding_ids}` |
| `research_subquery_finished` | Sub-Query abgeschlossen | `{project_id, run_id, sub_query_id, finding_count, status}` |
| `research_finding` | Jedes neue Finding | `{project_id, run_id, sub_query_id, finding_id, status, claim, sources, confidence, provider_key}` |
| `research_finding_updated` | Status-Wechsel | `{project_id, run_id, finding_id, status, confidence}` |
| `research_lateral_planned` | Lateral-Sub-Queries generiert | `{project_id, run_id, hop, entities, new_sub_queries}` |
| `research_budget` | **Druck-Schwellen-Übergang** (`ok → warn → tight → critical → extreme → exhausted`) | `{project_id, run_id, level, used, hard_cap, by_category, degradations_triggered}` |
| `research_token` | Streaming-Tokens (optional) | `{project_id, run_id, sub_query_id, text}` |
| `research_complete` | Terminal | `{project_id, run_id, status, counts, synapse_run_id?, token_usage, error?}` |

Alle Events nutzen das bestehende `sse_hub` (`backend/services/sse_hub.py:1-56`). Client filtert per `project_id` analog zu `SynapseGenerateBar.tsx:51-60`.

---

## 10. State-Modell (Findings-Lifecycle)

```
   candidate ── (Tier-B passes ≥0.7)  ──▶ grounded ──▶ persisted (KnowledgeItem)
       │                                      │
       │                                      ├── (Tier-C disagree) ──▶ flagged
       │                                      │                            │
       │                                      │                            ▼
       │                                      │                       review_queue
       │                                      ▼
       │                                (low conf <0.4) ──▶ rejected
       ▼
   (provider error) ──▶ failed
       ▼
   (run cancelled) ──▶ cancelled
```

**Tief-Mode-Spezial**: Findings im Status `grounded` mit `confidence ≥ 0.6` werden als **Eltern-Findings** für Lateral-Expansion in den nächsten Hop weitergegeben.

---

## 11. Routing-Rubrik (Planner-Prompt)

Der Planner-Prompt erhält:
1. User-Topic
2. **Tiefen-Modus** + zugehöriges Profil-Limit
3. **Aktivierte Provider** mit `description` + `typical_latency` + `side_effect`
4. Per-Projekt `routing_hints`
5. Top-5 KnowledgeItems als Kontext

Routing-Tabelle (im Prompt eingebettet):

| Frage-Typ | Empfohlene Provider |
|-----------|---------------------|
| Architektur/Design | `kb_fts`, `confluence`, `code_graph`, `project_documents` |
| Code/Implementierung | `code_graph`, `github`, `kb_fts` |
| Incidents/Bugs | `jira`, `log_servers`, `iq` |
| Kommunikation/Entscheidung | `webex`, `email`, `chat_history`, `project_notes` |
| Prozess/Policy | `handbook`, `confluence` |
| Build/Deploy | `jenkins`, `github`, `log_servers` |
| Library/Framework | `mcp_context7`, `web` (sanitized) |
| Compliance/Lizenzen | `iq`, `confluence` |
| Allgemeines Projektwissen | `kb_fts`, `project_documents`, `project_notes` |

**Output-Schema (strikt validiert, sonst Retry)**:
```json
{
  "sub_queries": [
    {
      "id": "sq1",
      "question": "...",
      "providers": ["..."],
      "priority": 1,
      "rationale": "..."
    }
  ],
  "budget_estimate": {
    "providers": 5,
    "llm_calls": 18
  }
}
```

Constraint: `len(sub_queries) ≤ profile.max_initial_sub_queries`, `len(providers) per sub_query ≤ profile.max_providers_per_sub_query`. Verletzung → Auto-Trim + Warnung im Log.

---

## 12. Validierungs-Schicht (Wiederverwendung)

### 12.1 Refactor: `synapse_validation.py` → `claim_aggregation.py`

Pure Funktionen aus `synapse_validation.py` werden extrahiert:
- `_aggregate_claim(grounding, critic_votes)` → `claim_aggregation.aggregate_claim()`
- `compute_confidence(claims)` → `claim_aggregation.compute_confidence()`
- `decide_verdict(confidence, has_contradiction)` → `claim_aggregation.decide_verdict()`
- `select_verifier_models(models, n)` → `claim_aggregation.select_verifier_models()`

`synapse_validation.validate_synapse()` und neue `research_validation.validate_finding()` rufen beide diese pure Logik.

### 12.2 Validierungs-Strategie pro Tiefe

| Aspekt | Normal | Tief |
|--------|--------|------|
| Tier-B Grounding | ja, alle Claims | ja, alle Claims |
| Tier-C Critic-Fan-out | nur bei `contradicted` oder Tier-B-Score < 0.5 | **immer** alle escalierte+borderline Claims |
| Critic-Modelle | 1 Modell | 3 Modelle (heterogen, aus `settings.research.critic_models`) |
| Verdict-Cutoffs | high≥0.7, medium≥0.4 | high≥0.75, medium≥0.5 (strenger) |

### 12.3 Pre-Insert vs. Post-Insert

Findings werden **direkt nach Validation** persistiert (kein Quarantäne-Schritt). Status:
- `grounded` (Tier-B passed, kein Tier-C) → `KnowledgeItem(confidence="medium")`
- `validated_high` (Tier-C consensus) → `KnowledgeItem(confidence="high")`
- `flagged` (Tier-C dissent) → `KnowledgeItem(confidence="low")` + ReviewQueue-Entry
- `rejected` (Contradiction) → kein KnowledgeItem, nur Finding-Row mit `status="rejected"`

User kann manuell `accept`/`reject` über die API.

---

## 13. Frontend-Design

### 13.1 ResearchDialog (erweitert)

```
┌─ Recherche starten ─────────────────────────────────────────┐
│  Topic:  [______________________________________________]   │
│                                                              │
│  Modus:  ( ) Single-Shot (Legacy)                            │
│          (●) Auto-Mode (parallel + validiert)                │
│                                                              │
│  Tiefe:  (●) Normal   ( ) Tief                               │
│          ┌─────────────────────────────────────────┐         │
│          │ Normal:                                  │         │
│          │  • 3-5 Sub-Queries                       │         │
│          │  • ~12 LLM-Calls, ~3 min                 │         │
│          │  • Standard-Validierung                  │         │
│          ├─────────────────────────────────────────┤         │
│          │ Tief:                                    │         │
│          │  • 6-8 Sub-Queries + bis zu 2 Lateral-   │         │
│          │    Hops ("links und rechts schauen")     │         │
│          │  • ~30 LLM-Calls, ~7 min                 │         │
│          │  • Strenge Validierung (Critic-Fan-out)  │         │
│          │  • Inkl. Synapse-Synthese                │         │
│          └─────────────────────────────────────────┘         │
│                                                              │
│  Quellen: [ Quellen-Auswahl bearbeiten ▾ ]                   │
│           ✓ KB · ✓ Documents · ✓ Confluence · ✓ Email ...    │
│                                                              │
│                                       [Abbrechen]  [▶ Start] │
└──────────────────────────────────────────────────────────────┘
```

### 13.2 ResearchAutoBar (Live-Phasen-View)

```
┌─ Auto-Mode-Recherche (🔍🔍 Tief) ──────────────────────────┐
│  Topic: "OAuth2 PKCE in Service X"           [⏸ Cancel]   │
│                                                             │
│  ●━━━●━━━●━━━●━━━○━━━○━━━○                                 │
│  Plan  Search Extract Lateral Validate Persist Synth        │
│                                          Hop 1/2            │
│                                                             │
│  Sub-Queries (Round 0):                                     │
│   ✓ Welche Auth-Methoden nutzt Service X?     • 3 Findings │
│   ✓ PKCE-Code-Pfade in Service X?             • 5 Findings │
│   ✓ Gab es zuletzt Auth-Incidents?            • 2 Findings │
│   ✓ Welche Entscheidungen wurden getroffen?   • 4 Findings │
│                                                             │
│  🔄 Lateral-Hop 1 — extracted 5 entities, planning...      │
│   ↪ keycloak-broker, refresh-token policy, v4.2, ...       │
│                                                             │
│  Findings Live-Stream (14 total / 9 grounded / 1 flagged): │
│   • [✓ 0.91 high]  Service X nutzt PKCE seit v4.2          │
│       └─ confluence:page-456, code:auth.py:88              │
│   • [⚠ 0.52 low]   Refresh-Tokens 90d                      │
│       └─ flagged: contradiction in kb:item-77              │
│   • [● 0.78 med]   keycloak-broker config                  │
│       └─ ↪ Round 1 / Round 0:F1                            │
│   • [○ ...]        (streamend)                             │
└─────────────────────────────────────────────────────────────┘
```

### 13.3 Sources-Settings-Panel

```
┌─ Wissens-Quellen (Projekt X) ───────────────────────────────────────┐
│  Default-Tiefe: ( ) Normal   (●) Tief                                │
│                                                                       │
│  Lokale Quellen (ProjectHub-DB)                                      │
│  ☑ ProjectHub-Knowledge-Base   • 247 Items                          │
│  ☑ Projekt-Dokumente            • 12 docx/pdf                       │
│  ☑ Projekt-Notizen              • 89 Notes                          │
│  ☑ Chat-Verlauf                 • 14 Sessions                       │
│                                                                       │
│  Interne Systeme (via AI-Assist)        Status     Aktion            │
│  ☑ Confluence            🟢 OK   Default-Spaces: [TEAM] [+]          │
│  ☑ Email                 🟢 OK   Tage zurück: [30]                   │
│  ☑ Webex                 🟢 OK   Räume: [Picker ▾]                   │
│  ☐ Jira                  🟢 OK   Default-Projekt: [____]             │
│  ☐ Log-Server            ⚪ nicht konfiguriert                       │
│  ☑ Handbook              🟢 OK                                       │
│  ☐ Code-Graph            🟢 OK   Sprache: ◉ Java ○ Python            │
│  ☐ GitHub                🟢 OK   Default-Repo: [____]                │
│  ☐ Jenkins               🟢 OK                                       │
│  ☐ Sonatype IQ           🟢 OK   App-ID: [____]                      │
│  ☐ Message Queues        🟢 OK                                       │
│                                                                       │
│  Externe Quellen                                                     │
│  ☐ Web-Suche (DuckDuckGo)   ⚠ Sanitization-Pflicht                  │
│  ☐ MCP Context7 (Library-Docs)                                       │
│  ☐ Google Drive             ◯ Auth ausstehend  [Verbinden]          │
│  ☐ Gmail                    ◯ Auth ausstehend  [Verbinden]          │
│                                                                       │
│  Budget (überschreibt Profile)                                       │
│  Normal:  Max Sub-Queries: [5]   Max LLM-Calls: [12]   Timeout: [180s]│
│  Tief:    Max Sub-Queries: [8]   Max LLM-Calls: [30]   Timeout: [420s]│
│           Max Lateral-Hops: [2]  Per-Hop-Subqueries: [6]              │
│                                                                       │
│  Routing-Hinweise (frei für Planner):                                │
│  [Bei Auth-Themen immer Confluence vor Code-Graph._____________]     │
│                                                                       │
│                                              [Test alle]  [Speichern]│
└──────────────────────────────────────────────────────────────────────┘
```

### 13.4 Frontend-Stores

```typescript
// stores/researchStore.ts
interface ResearchStore {
  runsByProject: Record<string, ResearchRun[]>
  activeRunByProject: Record<string, ResearchRun | null>
  findingsByRun: Record<string, ResearchFinding[]>
  subQueriesByRun: Record<string, ResearchSubQuery[]>
  liveProgressByRun: Record<string, ResearchProgress | null>

  startRun: (pid: string, opts: StartRunOpts) => Promise<{run_id, started, reason?}>
  fetchRun: (runId: string) => Promise<void>
  cancelRun: (runId: string) => Promise<void>
  acceptFinding: (runId: string, fid: string) => Promise<void>
  rejectFinding: (runId: string, fid: string, note?: string) => Promise<void>

  // SSE-Hooks
  onProgress: (event: ResearchProgressEvent) => void
  onFinding: (event: ResearchFindingEvent) => void
  onFindingUpdate: (event: ResearchFindingUpdateEvent) => void
  onLateralPlanned: (event: ResearchLateralPlannedEvent) => void
  onComplete: (event: ResearchCompleteEvent) => void
}
```

---

## 14. Phasenplan (final, mit Tiefen-Integration)

| # | Phase | Dauer | Deliverables | Tiefe-Anteil |
|---|-------|-------|--------------|--------------|
| 0 | Validation-Library extrahieren | 1.5 d | `claim_aggregation.py` + Tests; Synapse-Tests bleiben 28/28 grün | — |
| 1 | Models + Run-State | 0.5 d | `ResearchRun`, `ResearchSubQuery`, `ResearchFinding`, `ProjectResearchSettings` mit `depth`, `hop`, `is_lateral`, `entity_focus`, `relevance_score`, `parent_finding_ids` | ja |
| 2 | Provider-ABC + lokale Provider | 1.5 d | `SearchProvider`-Protocol, `kb_fts`, `project_documents`, `project_notes`, `chat_history` | — |
| 3 | AI-Assist-Streaming-Bridge | 1 d | `_streaming.stream_agent_tool()`, Tool-Blacklist | — |
| **3b** | **BM25-Prefilter + LLM-Rerank-Service** | **1 d** | `research_providers/_bm25.py` (rank-bm25 oder Eigen-Impl) + `services/research_rerank.py` (Batch-Prompt, JSON-Score-Parse) + Tests | ja |
| **3c** | **BudgetTracker** | **1 d** | `services/research_budget.py` (TokenBudgetPolicy, BudgetTracker, Reservation/Commit, Pressure-Level, Auto-Degradation-Hooks, adaptive Erweiterung) + Property-Tests | ja |
| 4 | Internal-Provider Tier-1-Sprint | 2 d | `confluence`, `confluence_search`, `email`, `webex`, `jira`, `handbook` — **alle nutzen Rerank-Pipeline via 3b** | — |
| 5 | Internal-Provider Tier-2-Sprint | 1.5 d | `log_servers`, `code_graph`, `iq`, `github`, `jenkins`, `mq` | — |
| 6 | Planner + Pipeline | 2 d | `research_planner.plan_subqueries()` (tiefe-aware), `research_pipeline.run_research()`, SSE-Events incl. `research_budget`, Semaphor, Cancel, **BudgetTracker-Wiring** (jeder LLM-Call via `_llm_call_with_budget`) | ja |
| **7** | **Lateral-Expansion (Tief-only)** | **2 d** | `research_lateral.expand()` mit Entity-Extract (`synapse_entities.py` reuse) + Relevance-Ranking + Lateral-Planner + Hop-Loop | **ja, core** |
| 8 | Validation-Hookup | 1.5 d | Tier-B per Finding, Tier-C nach Tiefen-Profil. ReviewQueue-Integration | ja |
| 9 | Inline-Synthesis | 1 d | Bei `auto_synthesise=true`: `run_synapse_generation(scope_item_ids=new_ids)` | ja |
| 10 | Router + API | 1 d | `routers/research.py` mit `depth`-Param, `routers/project_research_settings.py` | ja |
| 11 | Frontend Settings-UI | 2 d | `SourcesPanel.tsx`, Health-Pings, Default-Depth-Toggle | ja |
| 12 | Frontend Auto-Bar + Stream | 2.5 d | `ResearchAutoBar`, `SubQueryStrip`, `FindingsStream`, `FindingDetail`, Lateral-Visualisierung | ja |
| 13 | External-Provider (optional) | 1 d | `web` (sanitize-Vorpfad), `mcp_context7` | — |
| 14 | MCP-Auth-Bridge (optional) | 1 d | `google_drive`, `gmail` Stub + Auth-Bridge | — |
| 15 | E2E + Hardening | 1.5 d | Fake-AI-Assist TestClient, Idempotenz, Cancel-Flow, Lateral-Runaway-Test | ja |

**Critical-Path (ohne Phase 13/14)**: ca. 20 Tage netto. Phase 7 ist neu und der größte Tief-spezifische Aufwand.

**Parallelisierung**: Phase 2/3/4/5 können parallel mit Phase 6 starten, sobald die ABC steht.

---

## 15. Test-Matrix

| Bereich | Test-Typ | Beispiele |
|---------|----------|-----------|
| `claim_aggregation.py` (extrahiert) | Unit pure | Vorhandene 20/20 Synapse-Tests + 5 neue für Claim-only-Path |
| **`research_bm25.py`** | Unit | Score-Ordering, Stopword-Handling, leere Query, Sonderzeichen |
| **`research_rerank.py`** | Unit | Batch-Prompt-Aufbau, JSON-Score-Parse, Malformed-Output-Retry, Batch-Aufteilung bei > 15 Chunks |
| **`research_budget.py`** | Unit + Property | Reservation/Commit, Pressure-Level-Übergänge, Auto-Degradation-Ladder pro Stufe, Rerank-Exempt-Pfad, adaptive Erweiterung Cap, Concurrent-Reservation-Race |
| **Budget-E2E** | Integration | Künstlich Budget tight setzen → prüfe: Critic gedroppt → Synthesis gedroppt → Hop-2 gedroppt → `status=partial` (nicht `error`) |
| `research_lateral.py` | Unit | Entity-Dedup, Relevance-Cutoff, Hop-Limit, Budget-Cap |
| Tief-Lateral E2E | Integration | 2 Hops mit Mock-Provider, prüfe `parent_finding_ids`-Lineage |
| Tief-Runaway-Guard | Property | Lateral-Expansion mit 100 fake Entitäten → max 6 sub-queries pro Hop |
| Provider-Adapter | Unit | Jeder Provider: Streaming-Parse, Timeout, 5xx-Fehler, Cancel-Mid-Stream |
| Streaming-Bridge | Unit | Tool-Result-Mapping, Blacklist (5 verbotene Tools), Cancel via Event |
| Planner | Unit | Output-Schema, Profile-Limits (normal vs tief), Tiefen-spezifische Sub-Query-Count, Routing-Rubrik |
| Pipeline | Integration | Vollständiger Run mit 3 Mock-Providern, Normal vs Tief, Concurrency-Short-Circuit, Cancel |
| Router | API (TestClient) | `depth`-Param-Validation, `already_running`-Pfad, Findings-Accept/Reject |
| Provider-Health | Integration | Pro aktivierter Provider: `enabled+unauth` → `auth_missing`; `enabled+ok` → `ok` |
| Frontend `ResearchAutoBar` | Vitest | Phase-Stepper inkl. Lateral-Hop, SSE-Wiring, Cancel-Button |
| Frontend `SourcesPanel` | Vitest | Provider-Toggle, Save → API, Test-Buttons |
| E2E | Pytest + TestClient + Fake-AI-Assist | Normal-Mode: 4 Sub-Queries → 9 Findings → 5 KB-Items. Tief-Mode: 6 initial + 4 lateral → 14 Findings → 10 KB-Items + Synapse-Run |

---

## 16. Risiken und Mitigation

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|--------|-------------------|--------|------------|
| **Lateral-Runaway**: Entitäten-Explosion lässt Tief-Mode unendlich expandieren | Mittel | Hoch | Hard-Cap `max_lateral_hops=2`, Cap `max_lateral_sub_queries=6` pro Hop, Entity-Dedup, Budget-Check vor jedem Hop. |
| **LLM-Proxy-Flooding** durch N×M parallele Calls (N Sub-Queries × M Provider × K Critics) | Hoch | Hoch | Globaler Semaphor `max_concurrent_searches=4`, pro Run `max_llm_calls` hart-cap. |
| **Halluzinationen rutschen trotz Validierung durch** | Mittel | Mittel | Tier-B + Tier-C, im Tief-Mode strenger (heterogene Modelle). Slot für echtes NLI-Modell bleibt offen (siehe Synapse-Memory). |
| **Sub-Query-Decomposition irrelevant** (Planner halluziniert) | Mittel | Mittel | Output-Schema-Validation + 1× Retry mit korrigierendem Prompt. UI lässt manuelle Sub-Query-Override zu. |
| **Entity-Extraktion produziert Müll** (Stopwords, Single-Chars) | Mittel | Niedrig | Filter `min_length=3`, Blacklist, Frequency≥2-or-Conf≥0.8-Regel. |
| **Tief-Mode hängt zu lang** | Mittel | Mittel | Hard-Timeout 420s, Cancel-Knopf, Phase-by-Phase-Persistence (partial-results bleiben erhalten). |
| **SSE-Channel-Flooding** mit Token-Events bei vielen parallelen Runs | Niedrig (lokal Single-User) | Niedrig | Token-Events optional, Default-OFF; Heartbeat statt vollem Stream. |
| **AI-Assist `agent_stream` Timeout** auf langlaufendem Tool | Mittel | Mittel | Pro Provider `timeout_sec` aus Settings; Soft-Cancel via `cancel`-Event; Provider-Fail markiert Sub-Query als `failed`, Run läuft weiter. |
| **Race auf Project-Concurrency** | Niedrig | Niedrig | Short-Circuit `status=running` (`routers/synapse.py:212-220` Pattern). |
| **Credentials-Leak via Frontend** | Niedrig | Hoch | ProjectHub speichert keine Credentials. Health-Status enthält nur `{ok: bool, detail: "kategorisch"}`, nie Auth-Details. |
| **Reasoning-Continuity-Gap** trifft auch Auto-Mode (siehe Memory `project_reasoning_continuity_gap.md`) | Hoch | Niedrig (für diesen Use-Case) | Bekannt; nicht in diesem Sprint lösen. `reasoning_effort` für Planner+Critics optional aktivieren. |
| **Provider-Inventory veraltet** (Tool-Namen ändern sich in AI-Assist) | Mittel | Mittel | Pre-Flight-Check beim Start: `GET /api/v2/agent/tools` → vergleiche mit erwarteter Liste, Log-Warning bei Drift. |
| **Rerank-Batch-Output malformed JSON** | Mittel | Niedrig | 1× Retry mit korrigierendem Prompt; bei zweitem Fail Fallback auf BM25-only für diese Sub-Query. |
| **Token-Schätzungen weichen stark vom Actual ab** | Mittel | Mittel | Reservation nur als Vorab-Check; Commit nutzt **echte** `usage.total_tokens` aus AI-Assist-Response. Schätzfehler-Sammelwert pro Run protokolliert; bei > 30 % Drift Schätzungen recalibrieren. |
| **Rerank-Self-Limit floodet bei sehr großer Quelle** | Niedrig | Mittel | `max_batches=10` Cap → 150 Chunks max in Rerank; Rest wird vom Provider-internen BM25-Output abgeschnitten BEVOR Rerank überhaupt anläuft. |
| **Auto-Degradation droppt zu früh** (User wollte Tier-C wirklich) | Niedrig | Niedrig | Pressure-Schwellen pro Projekt überschreibbar; Run-Summary listet `degradations_triggered` transparent. |

---

## 17. Sicherheits- und Cost-Boundary

| Boundary | Mechanismus |
|----------|-------------|
| **Credentials** | ProjectHub speichert keine. Alle Auth → AI-Assist `credential_ref`. |
| **Write-Operations** | Hardcoded-Blacklist im Streaming-Helper: `iq_create_waiver`, `jenkins_trigger_build`, `email_send`, `webex_send`, `mq_publish`. |
| **Web-Provider** | Pflicht-Sanitize via `/api/research/sanitize`; bei `is_safe_for_web=false` → Provider übersprungen + Finding `status=blocked`. |
| **Rate-Limit pro Run** | `max_concurrent_searches` Semaphor + `max_providers_per_run` + **Token-Budget** (`TokenBudgetPolicy`, siehe §5.7) mit Auto-Degradation statt Hard-Crash. |
| **Project-Concurrency** | Ein laufender Run pro Projekt; zweiter Call → `already_running`. |
| **Per-Provider-Timeout** | `provider_settings.timeout_sec`, Hard-Cap = Profil-`hard_timeout_sec`. |
| **Cancel-Flow** | `asyncio.Event` durch alle Provider, zwischen Hops geprüft. Persistierte Findings bleiben erhalten. |
| **Idempotenz** | `(topic + provider_set + depth)` → SHA-Hash → Reuse bestehender Findings statt Neu-Suche (analog `knowledge.py:938-951`). |
| **Audit-Log** | Jeder Run schreibt vollständige `extra_data` mit Sub-Query-IDs, Provider, Tool-Calls, Validation-Verdicts. |

---

## 18. Offene Entscheidungen vor Kickoff

| # | Frage | Empfehlung | Status |
|---|-------|------------|--------|
| **D1** | Welche Provider liefern wir im ersten Sprint? | Lokale (4) + Confluence + Email + Webex + Jira + Code-Graph + Handbook. Rest later additiv. | offen |
| **D2** | `auto_synthesise` Default? | Normal-Mode: OFF · Tief-Mode: ON (im Profil festgeschrieben). | bestätigen |
| **D3** | Strenge der Tier-B-Validation | Lax: `supported|partial` → grounded. `unsupported` → flagged (nicht rejected). | offen |
| **D4** | Settings: pro Projekt vs. global? | Beides: Globale Defaults, pro-Projekt sparse-Overlay. | bestätigen |
| **D5** | Soll es einen dritten Modus "Quick" geben? | Vorerst nein. Single-Shot-Legacy bleibt für „schnellen Lookup". | offen |
| **D6** | Lateral-Hop default für `tief`? | 2 (Profil-Default). User kann pro Projekt auf 1 reduzieren. | offen |
| **D7** | Soll der User Sub-Queries vor Phase 2 editieren können? | Ja (Phase 12 UI): nach Planner-Output erscheint kurz ein Edit-Dialog mit Skip-Timer (5 s). | offen |
| **D8** | Token-Streaming-Events default? | OFF, opt-in per Setting. Sonst Bandbreitenverschwendung. | bestätigen |
| **D9** | Lateral-Entity-Extraction-Modell? | Reuse `synapse_entities.py` (schon getestet). Eigene Anpassung nicht nötig. | bestätigen |
| **D10** | Was passiert mit Findings bei `run cancelled`? | Bisher persistierte bleiben; in-flight Findings werden verworfen, Sub-Query-Status `cancelled`. | bestätigen |
| **D11** | Rerank-Strategie Default pro Tiefe | Normal=`bm25`, Tief=`bm25_llm`. User kann pro Projekt + pro Provider übersteuern. | bestätigen |
| **D12** | Hard-Cap-Höhe für Tief-Mode | 1 000 000 Tokens bei Soft-Cap 600 000. Erlaubt eine moderate Lateral-Hop-Runde mit fullem Critic-Fan-out, bevor Degradation einsetzt. | offen — ggf. nach erstem realen E2E-Run kalibrieren |
| **D13** | Wer entscheidet adaptive Budget-Erweiterung | Planner darf einmalig `budget_request: int` im Sub-Query-Output setzen (max +30 % vom hard_cap). Sonst keine. | bestätigen |
| **D14** | Token-Counting-Quelle | AI-Assist `usage.total_tokens` aus v2-Stream-Events ist Single-Source-of-Truth. Vorab-Schätzungen sind nur für Reservation, nicht für Commit. | bestätigen |
| **D15** | Brain-Stack-Konsumption | Auto-Mode nutzt `services/embedding/` + `services/retrieval/hybrid.py` + (sobald da) `services/retrieval/reranker.py` direkt; baut nichts parallel. `RerankStrategy.mode="auto"` wählt zur Laufzeit basierend auf Brain-Flags. | bestätigen |
| **D16** | Brain-T3.x-Wartezeit | Falls Brain-Reranker (T3.x) noch nicht fertig ist bei P3b-Start: temporärer Eigen-Adapter (`bm25_llm`-Pfad), später swappen. **Vereinbart bei Brain-Owner**: T3.x ist auf Brain-Roadmap. | offen |
| **D17** | Embedding-Backfill für Auto-Mode-Findings | Sollen frisch persistierte `KnowledgeItem`s (`source_type="research_auto"`) auto-embedded werden via Brain-T2.6-Backfill, oder erst on-demand? | offen — Empfehlung: auto-backfill via existing T2.6-Job |

---

## 19. Memory & Doku-Pflege

Nach Kickoff folgende Memory-Einträge anlegen/aktualisieren:

| Memory-Datei | Inhalt |
|--------------|--------|
| `project_research_auto_mode.md` (neu) | Fix-Plan mit gewählter Tiefen-Struktur, Phasen, Provider-Set, Entscheidungen D1-D10 |
| `project_projecthub.md` (update) | Verweis auf neues Research-Modul + Auto-Mode |
| `MEMORY.md` (update) | Index-Eintrag |

Während der Implementierung:
- Pro Phase ein Status-Update in `project_research_auto_mode.md` (Tests-grün, was implementiert, Abweichungen vom Plan)
- Bei Provider-Drift: `project_research_auto_mode.md` mit Notiz, dass Tool-Name in AI-Assist sich geändert hat

---

## 20. Verifizierungs-Checkliste vor Kickoff

- [ ] AI-Assist erreichbar; `GET /api/v2/agent/tools` listet erwartete Tools
- [ ] `POST /api/research/classify` und `/sanitize` antworten
- [ ] `POST /api/research/confluence` reagiert mit 200 (Smoke ohne echte Daten)
- [ ] Synapse-Tests 28/28 grün (Baseline)
- [ ] D1-D10 mit User durchgesprochen oder bewusst defaults übernommen
- [ ] Branch `feat/research-auto-mode` erstellt
- [ ] Memory-Eintrag `project_research_auto_mode.md` als Draft angelegt

---

## Anhang A — Beispiel-Aufruf (Tief-Mode)

**Request**:
```http
POST /api/research/proj-abc/runs
Content-Type: application/json

{
  "topic": "OAuth2 PKCE Implementation in Service X",
  "depth": "tief",
  "mode": "auto"
}
```

**Response (202)**:
```json
{ "run_id": "r-7f3a", "started": true, "depth": "tief" }
```

**Erwarteter Verlauf (über SSE)**:
```
event: research_progress
data: { "run_id": "r-7f3a", "phase": "planning", "hop": 0 }

event: research_progress
data: { "run_id": "r-7f3a", "phase": "searching", "hop": 0, "total": 6 }

event: research_subquery_started
data: { "sub_query_id": "sq1", "providers": ["confluence","code_graph"], "is_lateral": false }

event: research_finding
data: { "sub_query_id": "sq1", "finding_id": "f1", "status": "candidate", "claim": "...", "confidence": 0.91 }

... [mehrere finding-Events] ...

event: research_progress
data: { "phase": "lateral", "hop": 1 }

event: research_lateral_planned
data: { "hop": 1, "entities": ["keycloak-broker","refresh-token policy","v4.2"], "new_sub_queries": 3 }

event: research_subquery_started
data: { "sub_query_id": "sq7", "is_lateral": true, "parent_finding_ids": ["f1","f3"] }

... [Round 1 läuft] ...

event: research_progress
data: { "phase": "validating", "current": 8, "total": 14 }

event: research_finding_updated
data: { "finding_id": "f9", "status": "flagged", "confidence": 0.42 }

event: research_progress
data: { "phase": "synthesising" }

event: research_complete
data: {
  "status": "ok",
  "counts": { "sub_queries": 7, "findings": 14, "persisted": 10, "flagged": 2, "rejected": 1 },
  "synapse_run_id": "syn-r-9c2"
}
```

---

## Anhang B — Quellen-Inventory-Referenz

Vollständige Endpoint/Tool-Map aus AI-Assist (Stand 2026-05-16) ist im **Discovery-Bericht** in der Conversation dokumentiert und wird bei Sprint-Start gegen die laufende AI-Assist-Version verifiziert. Quellen ohne Tool/Endpoint:
- Tier 4 (`google_drive`, `gmail`) braucht MCP-Bridge im ProjectHub-Backend (Phase 14).

---

**Ende der Spezifikation.**
