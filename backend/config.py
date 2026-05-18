from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # AI-Assist Backend
    ai_assist_url: str = "http://localhost:8000"
    ai_assist_timeout: int = 30
    ai_assist_llm_timeout: int = 120

    # Database
    db_path: str = str(Path(__file__).parent / "data" / "projecthub.db")

    # Polling
    polling_interval_minutes: int = 5
    polling_enabled: bool = True

    # Document scan — wie viele Chunks parallel per LLM extrahiert werden.
    # Höher = schneller, aber mehr gleichzeitige Last auf AI-Assist/LLM.
    doc_scan_concurrency: int = 4

    # Synapsen — Wissens-Synthese & Validierung.
    # verifier_models: Modellnamen für den Critic-Fan-out. MIT >=2 Einträgen
    # entsteht echte Antwort-Diversität (heterogene Modelle gegen "agreement
    # collapse"); leer oder 1 Eintrag → ein einzelner Critic-Call (kein
    # Fan-out, da identische Calls keine Diversität bringen).
    synapse_verifier_models: list[str] = []
    synapse_verifier_samples: int = 3        # max Critic-Samples pro Claim
    synapse_max_llm_concurrency: int = 6     # gleichzeitige LLM-Calls in der Validierung
    synapse_confidence_high: float = 0.85    # >= → Band "high", Verdikt "persist"
    synapse_confidence_review: float = 0.5   # < → Band "low", Verdikt "human_review"

    # Server
    host: str = "0.0.0.0"
    port: int = 5001
    cors_origins: list[str] = ["http://localhost:5001", "http://127.0.0.1:5001", "http://localhost:3001", "http://localhost:3000", "http://127.0.0.1:3000"]

    # UI
    theme: str = "dark"
    language: str = "de"
    kanban_columns: list[str] = ["backlog", "in_progress", "review", "done"]

    # Jira (optional — if unset, jira_project sources won't sync)
    jira_base_url: str = ""  # e.g. https://company.atlassian.net
    jira_email: str = ""     # API-token auth (cloud) requires account email
    jira_api_token: str = ""
    jira_timeout: int = 20

    # ── Memory Bridge (P1) — AI-Assist ↔ ProjectHub-Brain ────────────────
    # See ``claudedocs/bridge_openapi_20260516.yaml`` and
    # ``claudedocs/design_memory_systems_20260516.md`` §4.
    # ``memory_bridge_token``: optional shared-secret for the
    # X-Memory-Bridge-Token header. Empty (default) = no auth on the bridge
    # endpoints — acceptable for localhost/in-network use. Set via
    # ``PROJECTHUB_MEMORY_BRIDGE_TOKEN`` for remote deployments.
    memory_bridge_token: str = ""

    # ── Brain (P2+) — embedding-aware retrieval, all default OFF ─────────
    # Mirrors AI-Assist's ``engine_v2.*`` flags so both sides can be
    # toggled independently without code changes.
    brain_embedding_enabled: bool = False                  # P2
    brain_contextual_retrieval_enabled: bool = False       # P2
    brain_reranker_enabled: bool = False                   # P3
    brain_hierarchical_synapses_enabled: bool = False      # P5
    brain_incremental_update_enabled: bool = False         # P9
    brain_bitemporal_claims_enabled: bool = False          # P10

    model_config = {"env_prefix": "PROJECTHUB_"}


settings = Settings()
