"""End-to-end test for the Synapsen feature (Phases 1–4).

Drives the whole pipeline through the real HTTP API against a fresh
SQLite DB — only the external LLM is faked (``ai_assist.agent_call``),
per the rule that tests must not depend on external services.

Flow exercised:
    create project → seed KnowledgeItems → POST /generate →
    background pipeline (extract → detect → synthesise → validate) →
    GET runs / synapses / detail / review-queue → POST /ask →
    POST /review

The fake LLM dispatches on distinctive phrases in each prompt and
returns canned JSON crafted so the run produces two communities:
  - an "Authentifizierung/OAuth2" cluster → all claims supported → persist
  - a "Deployment/Docker" cluster → one contradicted claim → human_review
"""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient


# --- Fake LLM ---------------------------------------------------------------

def _resp(payload: dict) -> dict:
    """Shape an ``agent_call`` return value around a canned JSON payload."""
    return {
        "response": json.dumps(payload, ensure_ascii=False),
        "model": "fake-llm",
        "usage": {"total_tokens": 50},
        "error": None,
    }


def _claim_line(prompt: str) -> str:
    """The ``AUSSAGE:`` line of a grounding/critic prompt — dispatch on the
    claim itself, not the source excerpts that follow it (those repeat the
    raw item text and would otherwise match the wrong branch)."""
    for line in prompt.splitlines():
        if line.startswith("AUSSAGE:"):
            return line
    return ""


async def _fake_agent_call(*, session_id, message, model=None,
                           project_path=None, auto_detect=True):
    """Stand-in for ``ai_assist.agent_call`` — dispatches on the prompt."""
    t = message
    is_deployment = "Docker" in t or "Deployment" in t

    # Order matters: the critic prompt also contains "Faktenprüfer".
    if "skeptischer Faktenprüfer-Kritiker" in t:
        claim = _claim_line(t)
        relation = (
            "contradicted"
            if ("Rollback" in claim or "automatisch" in claim)
            else "supported"
        )
        return _resp({
            "claim_quote": "x", "evidence_quote": "y",
            "relation": relation, "reasoning": "test",
        })
    if "strenger Faktenprüfer" in t:
        claim = _claim_line(t)
        if "OAuth2" in claim:  # auth claim 1 → trusted, skips the critic
            return _resp({
                "relation": "supported", "score": 0.9,
                "evidence_source": 1, "evidence_span": "OAuth2",
            })
        return _resp({  # everything else → partial → escalates to the critic
            "relation": "partial", "score": 0.5,
            "evidence_source": 1, "evidence_span": "...",
        })
    if "Wissens-Synthetisierer" in t:
        if is_deployment:
            return _resp({
                "title": "Deployment via Docker",
                "summary": "Das Projekt liefert über Docker-Container aus.",
                "claims": [
                    {"text": "Das Deployment nutzt Docker-Container.", "sources": [1]},
                    {"text": "Rollbacks erfolgen automatisch.", "sources": [1, 2]},
                ],
            })
        return _resp({
            "title": "Authentifizierung über OAuth2",
            "summary": "Die Authentifizierung basiert durchgängig auf OAuth2.",
            "claims": [
                {"text": "Die Authentifizierung erfolgt über OAuth2.", "sources": [1]},
                {"text": "Tokens haben eine begrenzte Lebensdauer.", "sources": [1, 2]},
            ],
        })
    if "neu extrahierte Entitäten" in t:  # entity adjudication — nothing ambiguous
        return _resp({"decisions": []})
    if "Wissens-Analyst" in t:  # entity extraction
        if is_deployment:
            return _resp({
                "entities": [
                    {"name": "Deployment", "type": "process", "description": "Auslieferung"},
                    {"name": "Docker", "type": "technology", "description": "Container-Runtime"},
                ],
                "relations": [
                    {"source": "Deployment", "target": "Docker", "description": "nutzt"},
                ],
            })
        return _resp({
            "entities": [
                {"name": "Authentifizierung", "type": "concept", "description": "Login"},
                {"name": "OAuth2", "type": "technology", "description": "Auth-Protokoll"},
            ],
            "relations": [
                {"source": "Authentifizierung", "target": "OAuth2", "description": "nutzt"},
            ],
        })
    if "Beantworte die FRAGE" in t:
        return _resp({
            "answer": "Die Authentifizierung läuft über OAuth2.",
            "sources": [1],
        })
    return _resp({})


# --- Fixtures ---------------------------------------------------------------

@pytest.fixture
def client():
    """A TestClient on a freshly-created throwaway DB (lifespan runs init_db)."""
    from config import settings

    for ext in ("", "-wal", "-shm"):
        path = settings.db_path + ext
        if os.path.exists(path):
            os.remove(path)

    from main import app
    with TestClient(app) as test_client:
        yield test_client


_AUTH_ITEMS = [
    {
        "title": "Login-Flow",
        "content": "Der Login nutzt OAuth2 mit Token-Refresh für die Authentifizierung.",
        "category": "architecture",
    },
    {
        "title": "Token-Lebensdauer",
        "content": "Access-Tokens laufen nach einer Stunde ab. Der Refresh erfolgt über OAuth2.",
        "category": "architecture",
    },
]
_DEPLOY_ITEMS = [
    {
        "title": "Deployment-Pipeline",
        "content": "Das Deployment erfolgt über Docker-Container in der CI-Pipeline.",
        "category": "infrastructure",
    },
    {
        "title": "Rollback-Strategie",
        "content": "Bei Fehlern rollt das Deployment automatisch zurück. Docker-Images werden versioniert.",
        "category": "infrastructure",
    },
]


# --- Test -------------------------------------------------------------------

def test_synapse_pipeline_e2e(client, monkeypatch):
    from services.ai_assist_client import ai_assist
    monkeypatch.setattr(ai_assist, "agent_call", _fake_agent_call)

    # --- seed: project + 4 knowledge items via the real API ---
    proj = client.post("/api/projects", json={"name": "E2E Synapse Test"})
    assert proj.status_code == 201, proj.text
    pid = proj.json()["id"]

    for item in _AUTH_ITEMS + _DEPLOY_ITEMS:
        r = client.post(f"/api/knowledge/{pid}", json=item)
        assert r.status_code == 201, r.text

    # --- trigger generation (non-blocking background run) ---
    gen = client.post(f"/api/synapse/{pid}/generate")
    assert gen.status_code == 200, gen.text
    gen_body = gen.json()
    assert gen_body["started"] is True
    run_id = gen_body["run_id"]

    # --- poll the run to completion ---
    run = None
    for _ in range(120):  # ~18s ceiling; fake LLM finishes in well under 1s
        run = client.get(f"/api/synapse/{pid}/runs/{run_id}").json()
        if run["status"] != "running":
            break
        time.sleep(0.15)
    assert run is not None and run["status"] == "ok", run
    assert run["item_count"] == 4
    assert run["entity_count"] == 4          # Authentifizierung, OAuth2, Deployment, Docker
    assert run["synapse_count"] == 2
    assert run["validated_count"] == 1       # auth cluster → persist
    assert run["review_count"] == 1          # deployment cluster → human_review

    # --- list synapses ---
    synapses = client.get(f"/api/synapse/{pid}/synapses").json()
    assert len(synapses) == 2
    persisted = [s for s in synapses if s["verdict"] == "persist"]
    review = [s for s in synapses if s["verdict"] == "human_review"]
    assert len(persisted) == 1 and len(review) == 1
    assert persisted[0]["confidence_band"] == "high"
    assert persisted[0]["confidence"] >= 0.85
    assert persisted[0]["claim_count"] == 2

    # --- synapse detail with the claim evidence trail ---
    detail = client.get(
        f"/api/synapse/{pid}/synapses/{persisted[0]['id']}"
    ).json()
    assert len(detail["claims"]) == 2
    assert all(c["relation"] == "supported" for c in detail["claims"])
    # Claim 1 was trusted at the grounding tier (no critic), claim 2 escalated.
    assert any(c["verifier_votes"] == {} for c in detail["claims"])
    assert any(c["verifier_votes"] for c in detail["claims"])

    # --- review queue holds exactly the human_review synapse ---
    queue = client.get(f"/api/synapse/{pid}/review-queue").json()
    assert len(queue) == 1
    assert queue[0]["synapse_id"] == review[0]["id"]

    # --- corpus-wide /ask works off the validated synapse ---
    ask = client.post(
        f"/api/synapse/{pid}/ask",
        json={"question": "Wie funktioniert die Authentifizierung?"},
    ).json()
    assert ask["answer"]
    assert len(ask["sources"]) >= 1

    # --- human review action closes the queue item ---
    reviewed = client.post(
        f"/api/synapse/{pid}/synapses/{review[0]['id']}/review",
        json={"verdict": "accepted"},
    ).json()
    assert reviewed["verdict"] == "persist_flagged"
    assert client.get(f"/api/synapse/{pid}/review-queue").json() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
