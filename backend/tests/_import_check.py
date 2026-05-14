"""Throwaway import/registration smoke check for the synapse modules.

Run directly: ``python tests/_import_check.py``. Not a pytest test — it
verifies the new models register their tables and every synapse service
module imports cleanly (catches bad imports the broken venv would hide).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: F401
from database import Base

_EXPECTED = {
    "knowledge_entities", "knowledge_entity_mentions",
    "knowledge_entity_relations", "synapses", "synapse_claims",
    "synapse_generation_runs", "knowledge_review_queue",
}
registered = set(Base.metadata.tables) & _EXPECTED
missing = _EXPECTED - registered
assert not missing, f"tables not registered: {missing}"
print(f"OK  {len(registered)} synapse tables registered")

import services.synapse_llm  # noqa: F401
import services.synapse_entities  # noqa: F401
import services.synapse_communities  # noqa: F401
import services.synapse_synthesis  # noqa: F401
import services.synapse_validation  # noqa: F401
import services.synapse_pipeline  # noqa: F401
print("OK  all 6 synapse service modules import cleanly")

from services.synapse_validation import (  # noqa: F401
    select_verifier_models, compute_confidence, decide_verdict,
)
print("OK  pure-logic symbols importable")

# Router registers and exposes the expected routes.
from routers.synapse import router as synapse_router

_routes = {
    (m, r.path)
    for r in synapse_router.routes
    for m in getattr(r, "methods", set())
}
_expected = {
    ("POST", "/api/synapse/{project_id}/generate"),
    ("GET", "/api/synapse/{project_id}/runs"),
    ("GET", "/api/synapse/{project_id}/runs/{run_id}"),
    ("GET", "/api/synapse/{project_id}/synapses"),
    ("GET", "/api/synapse/{project_id}/synapses/{synapse_id}"),
    ("DELETE", "/api/synapse/{project_id}/synapses/{synapse_id}"),
    ("POST", "/api/synapse/{project_id}/synapses/{synapse_id}/review"),
    ("GET", "/api/synapse/{project_id}/review-queue"),
    ("POST", "/api/synapse/{project_id}/ask"),
}
_missing = _expected - _routes
assert not _missing, f"router missing routes: {_missing}"
print(f"OK  synapse router exposes all {len(_expected)} expected routes")

# main.py wires the router into the app without import errors.
import main  # noqa: F401
_app_paths = {r.path for r in main.app.routes}
assert "/api/synapse/{project_id}/generate" in _app_paths, "router not registered in main.app"
print("OK  synapse router registered in main.app")
