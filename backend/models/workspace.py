"""Workspace-path mapping for the AI-Assist ↔ ProjectHub memory bridge.

A ProjectHub project can be the home for facts that originate in many AI-Assist
sessions, each running in its own filesystem location (the user's "workspace").
``ProjectWorkspacePath`` records the explicit (project_id, workspace_path) tuples
the bridge resolves against.

The Bridge endpoint ``POST /api/memory/v1/extract`` resolves an inbound
``workspace`` string to a ``project_id`` via the fallback chain (Design §4.4):

    1. exact match  → ``project_workspace_paths.workspace_path == workspace``
    2. prefix match → workspace startswith a registered path (longest wins)
    3. legacy fallback → ``projects.repo_path == workspace``
    4. None → caller gets 422 with the list of known workspaces

Composite primary key (project_id, workspace_path) keeps the row count bounded
without an extra surrogate id. ``workspace_path`` is canonicalised to
forward-slash + no trailing slash before insert — Windows / WSL / OneDrive
duplicates (e.g. ``C:\\Users\\...\\AI-Assist`` vs ``C:/Users/.../AI-Assist``)
must NOT create two rows.
"""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonicalize_workspace_path(path: str) -> str:
    """Normalise a workspace path for storage and lookup.

    - backslashes → forward slashes (Windows path round-tripping)
    - drop trailing slash (so ``/a/b`` and ``/a/b/`` are the same row)
    - lowercase the drive letter on Windows (``C:`` vs ``c:``)

    No filesystem access — pure string normalisation.
    """
    if not path:
        return path
    p = path.replace("\\", "/").rstrip("/")
    # Lower-case Windows drive letter (``C:/Users/...`` → ``c:/Users/...``).
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        p = p[0].lower() + p[1:]
    return p


class ProjectWorkspacePath(Base):
    """One filesystem path that resolves to a project for the memory bridge."""

    __tablename__ = "project_workspace_paths"

    project_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_path: Mapped[str] = mapped_column(String(500), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
