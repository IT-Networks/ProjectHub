"""Provider registry — single import surface for the orchestrator.

``PROVIDERS`` is keyed by the same string the planner emits and the
Settings UI toggles. Adding a new provider = adding one line here +
shipping a module in this package.

The objects are instantiated eagerly at import time; they are stateless
adapters by design (per ``base.SearchProvider`` contract) so a module-
level singleton is fine. If a provider ever grows real state, switch
to a factory function and the caller pattern at the orchestrator-side
won't change.
"""
from __future__ import annotations

from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    SearchProvider,
    make_snippet,
)
from services.research_providers.chat_history import ChatHistoryProvider
from services.research_providers.kb_fts import KBFtsProvider
from services.research_providers.project_documents import ProjectDocumentsProvider
from services.research_providers.project_notes import ProjectNotesProvider

PROVIDERS: dict[str, SearchProvider] = {
    KBFtsProvider.key: KBFtsProvider(),
    ProjectDocumentsProvider.key: ProjectDocumentsProvider(),
    ProjectNotesProvider.key: ProjectNotesProvider(),
    ChatHistoryProvider.key: ChatHistoryProvider(),
}

__all__ = [
    "Finding",
    "ProviderHealth",
    "SearchProgress",
    "SearchProvider",
    "PROVIDERS",
    "make_snippet",
]
