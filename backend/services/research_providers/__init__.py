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
from services.research_providers.code_graph import CodeGraphProvider
from services.research_providers.confluence import ConfluenceProvider
from services.research_providers.confluence_search import ConfluenceSearchProvider
from services.research_providers.email import EmailProvider
from services.research_providers.github import GitHubProvider
from services.research_providers.handbook import HandbookProvider
from services.research_providers.iq import IQProvider
from services.research_providers.jenkins import JenkinsProvider
from services.research_providers.jira import JiraProvider
from services.research_providers.kb_fts import KBFtsProvider
from services.research_providers.log_servers import LogServersProvider
from services.research_providers.mq import MQProvider
from services.research_providers.project_documents import ProjectDocumentsProvider
from services.research_providers.project_notes import ProjectNotesProvider
from services.research_providers.webex import WebexProvider

PROVIDERS: dict[str, SearchProvider] = {
    # Tier 1 — local (always default on)
    KBFtsProvider.key: KBFtsProvider(),
    ProjectDocumentsProvider.key: ProjectDocumentsProvider(),
    ProjectNotesProvider.key: ProjectNotesProvider(),
    ChatHistoryProvider.key: ChatHistoryProvider(),
    # Tier 2 — internal via AI-Assist (default off; per-project opt-in)
    ConfluenceProvider.key: ConfluenceProvider(),
    ConfluenceSearchProvider.key: ConfluenceSearchProvider(),
    EmailProvider.key: EmailProvider(),
    WebexProvider.key: WebexProvider(),
    JiraProvider.key: JiraProvider(),
    HandbookProvider.key: HandbookProvider(),
    LogServersProvider.key: LogServersProvider(),
    CodeGraphProvider.key: CodeGraphProvider(),
    IQProvider.key: IQProvider(),
    GitHubProvider.key: GitHubProvider(),
    JenkinsProvider.key: JenkinsProvider(),
    MQProvider.key: MQProvider(),
}

__all__ = [
    "Finding",
    "ProviderHealth",
    "SearchProgress",
    "SearchProvider",
    "PROVIDERS",
    "make_snippet",
]
