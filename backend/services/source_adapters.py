"""Registry of source adapters.

Each adapter collects fresh data for ONE source_type, deduplicates against
the `source_changes` staging table, and inserts new rows. It returns the
number of NEW changes detected (already-seen items do not count).

Adapter signature:
    async def adapter(db, project_id: str, source: DataSourceLink) -> int

The adapter may commit multiple times; it owns its own transaction scope
within the passed session. Exceptions bubble up to the sync runner which
records them on `DataSourceLink.last_error_msg`.

Deduplication strategy (uniform across adapters):
    key   = (project_id, source_type, external_ref, payload_hash)
    hash  = sha256 over adapter-chosen canonical fields
    → same item + same content  → no row
    → same item + new content   → NEW row (previous stays as history)
"""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.project import DataSourceLink
from models.source_change import SourceChange
from services.ai_assist_client import ai_assist
from services.jira_client import jira_client, _text_from_adf
from services import git_client

logger = logging.getLogger("projecthub.source_adapters")


AdapterFn = Callable[[AsyncSession, str, DataSourceLink], Awaitable[int]]


ADAPTERS: dict[str, AdapterFn] = {}


def register(source_type: str):
    def decorator(fn: AdapterFn) -> AdapterFn:
        ADAPTERS[source_type] = fn
        return fn
    return decorator


async def run_adapter(db: AsyncSession, project_id: str, source: DataSourceLink) -> int:
    fn = ADAPTERS.get(source.source_type)
    if fn is None:
        logger.debug("No adapter for source_type=%s, skipping", source.source_type)
        return 0
    return await fn(db, project_id, source)


# --- Helpers -----------------------------------------------------------------

def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(fields: dict) -> str:
    """Stable SHA-256 hash over a JSON-serialized dict."""
    blob = json.dumps(fields, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


async def _seen_hashes_for_ref(
    db: AsyncSession,
    project_id: str,
    source_type: str,
    external_ref: str,
) -> set[str]:
    """All payload_hashes we've already staged for this external_ref."""
    res = await db.execute(
        select(SourceChange.payload_hash).where(
            SourceChange.project_id == project_id,
            SourceChange.source_type == source_type,
            SourceChange.external_ref == external_ref,
        )
    )
    return {r[0] for r in res.all()}


# --- github_repo adapter (S2) ------------------------------------------------

@register("github_repo")
async def github_repo_adapter(
    db: AsyncSession,
    project_id: str,
    source: DataSourceLink,
) -> int:
    """Collect new or updated pull requests for a linked GitHub repo.

    Requires AI-Assist endpoint ``GET /api/github/pulls`` which is not
    yet implemented upstream. The adapter therefore raises a clear
    RuntimeError that the sync runner surfaces as
    ``DataSourceLink.last_error_msg`` — sync continues for other
    source types.
    """
    cfg = source.config_dict
    owner = cfg.get("owner", "").strip()
    repo = cfg.get("repo", "").strip()
    if not owner or not repo:
        raise RuntimeError(f"github_repo source {source.id} missing owner/repo in config")

    raise RuntimeError(
        f"github_repo sync for {owner}/{repo} skipped — AI-Assist has no "
        "GET /api/github/pulls endpoint (planned, not implemented). "
        "Link the repo as github_pr (per-PR) for now."
    )

    # Unreachable — kept as a sketch for when the endpoint lands.
    # Delta-sync optimization: only ask for PRs updated since last successful sync
    since = source.last_synced_at if source.last_sync_status == "ok" else None  # noqa: F841

    prs: list = []  # would be: await ai_assist.get(.../api/github/pulls, params=...)

    new_count = 0
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if number is None:
            continue

        external_ref = f"{owner}/{repo}#{number}"
        # Canonical fields: if any of these change, we want a new staging row
        canonical = {
            "state": pr.get("state"),
            "title": pr.get("title"),
            "body": (pr.get("body") or "")[:4000],
            "updated_at": pr.get("updated_at"),
            "merged_at": pr.get("merged_at"),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "changed_files": pr.get("changed_files"),
            "comments": pr.get("comments", pr.get("review_comments")),
        }
        payload_hash = _hash(canonical)

        seen = await _seen_hashes_for_ref(db, project_id, "pr", external_ref)
        if payload_hash in seen:
            continue  # Already have this exact content

        # Prepare payload for analyzer consumption
        top_files = pr.get("top_files") or []
        body_snippet = (pr.get("body") or "").strip()[:1500]
        payload = {
            "title": pr.get("title") or "",
            "state": pr.get("state") or "",
            "author": (pr.get("user") or {}).get("login", "") if isinstance(pr.get("user"), dict) else str(pr.get("user", "")),
            "updated_at": pr.get("updated_at") or "",
            "additions": pr.get("additions") or 0,
            "deletions": pr.get("deletions") or 0,
            "changed_files": pr.get("changed_files") or 0,
            "body_snippet": body_snippet or "(keine Beschreibung)",
            "top_files": "\n".join(f"- {f}" for f in top_files[:10]) or "(nicht verfügbar)",
            "html_url": pr.get("html_url") or "",
            "owner": owner,
            "repo": repo,
            "number": number,
        }

        change = SourceChange(
            id=_gen_id(),
            project_id=project_id,
            source_link_id=source.id,
            source_type="pr",
            external_ref=external_ref,
            payload_hash=payload_hash,
            title=(pr.get("title") or external_ref)[:500],
            analysis_status="pending",
        )
        change.payload = payload
        db.add(change)
        await db.flush()
        new_count += 1

    if new_count:
        await db.commit()
    return new_count


# --- jenkins_job adapter (S3) ------------------------------------------------

# Jenkins color → human state. "_anime" suffix = currently building.
_JENKINS_COLOR_STATE = {
    "blue": "success",
    "red": "failure",
    "yellow": "unstable",
    "aborted": "aborted",
    "disabled": "disabled",
    "notbuilt": "notbuilt",
    "grey": "notbuilt",
}


def _jenkins_state(color: str | None) -> str:
    if not color:
        return "unknown"
    base = color.replace("_anime", "")
    return _JENKINS_COLOR_STATE.get(base, base or "unknown")


def _format_duration(ms: int | None) -> str:
    if not ms or ms <= 0:
        return "unbekannt"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


@register("jenkins_job")
async def jenkins_job_adapter(
    db: AsyncSession,
    project_id: str,
    source: DataSourceLink,
) -> int:
    """Collect new Jenkins builds for linked jobs.

    Uses existing AI-Assist endpoint GET /api/jenkins/jobs?path_name=X.
    For each matching job, stages one SourceChange per (build_number, state)
    that we have not seen before. If AI-Assist exposes /api/jenkins/build/...
    details, we also pull log_tail for richer analysis.
    """
    cfg = source.config_dict
    path_name = cfg.get("path_name", "").strip() or None
    job_filter = cfg.get("job_name", "").strip()

    data = await ai_assist.get_jenkins_jobs(path_name)
    if data is None:
        raise RuntimeError(
            f"AI-Assist did not return Jenkins jobs for path_name={path_name!r}"
        )

    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not jobs:
        return 0

    new_count = 0
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_name = job.get("name") or ""
        if job_filter and job_name != job_filter:
            continue

        last_build = job.get("lastBuild") or {}
        build_number = last_build.get("number")
        if build_number is None:
            continue  # Job without builds yet

        color = job.get("color") or ""
        state = _jenkins_state(color)
        # Skip in-progress builds — they'd generate a change now and another
        # when they finish. The post-build run will catch it.
        if color.endswith("_anime"):
            continue

        external_ref = f"{path_name or '(default)'}/{job_name}#{build_number}"
        canonical = {
            "build_number": build_number,
            "state": state,
            "result": last_build.get("result"),
            "timestamp": last_build.get("timestamp"),
        }
        payload_hash = _hash(canonical)

        seen = await _seen_hashes_for_ref(db, project_id, "build", external_ref)
        if payload_hash in seen:
            continue

        # Build-detail enrichment (log_tail, duration) needs an AI-Assist
        # endpoint that doesn't exist yet (GET /api/jenkins/build/{job}/{num}).
        # We continue without enrichment — duration comes from last_build,
        # log_tail stays empty.
        log_tail = ""
        duration_ms = last_build.get("duration") or 0

        payload = {
            "title": f"{job_name} #{build_number} — {state}",
            "state": state,
            "duration": _format_duration(duration_ms),
            "log_tail": log_tail or "(Log nicht verfügbar)",
            "job_name": job_name,
            "path_name": path_name or "",
            "build_number": build_number,
            "url": last_build.get("url") or job.get("url") or "",
            "html_url": last_build.get("url") or job.get("url") or "",
        }

        change = SourceChange(
            id=_gen_id(),
            project_id=project_id,
            source_link_id=source.id,
            source_type="build",
            external_ref=external_ref,
            payload_hash=payload_hash,
            title=payload["title"][:500],
            analysis_status="pending",
        )
        change.payload = payload
        db.add(change)
        await db.flush()
        new_count += 1

    if new_count:
        await db.commit()
    return new_count


# --- jira_project adapter (S4) -----------------------------------------------

# Jira fields we care about — keeps payload small
_JIRA_FIELDS = [
    "summary", "status", "priority", "issuetype",
    "description", "updated", "created",
    "assignee", "reporter", "labels",
    "comment",  # includes last N comments (ADF)
]


def _jira_field(issue: dict, name: str, default=None):
    return (issue.get("fields") or {}).get(name, default)


@register("jira_project")
async def jira_project_adapter(
    db: AsyncSession,
    project_id: str,
    source: DataSourceLink,
) -> int:
    """Collect new/updated Jira issues for a linked project key.

    Config shape: { "project_key": "PROJ" }

    Delta-sync via JQL `updated >= "<last_sync>"`. Each issue becomes a
    SourceChange of type "jira". Comments are included in the payload
    (not separate rows) — per user decision.
    """
    if not jira_client.configured:
        raise RuntimeError(
            "Jira ist nicht konfiguriert — setze PROJECTHUB_JIRA_BASE_URL, "
            "PROJECTHUB_JIRA_EMAIL, PROJECTHUB_JIRA_API_TOKEN"
        )

    cfg = source.config_dict
    project_key = (cfg.get("project_key") or "").strip()
    if not project_key:
        raise RuntimeError(f"jira_project source {source.id} missing project_key in config")

    # Build JQL. Delta-sync since last successful run.
    jql_parts = [f'project = "{project_key}"']
    if source.last_synced_at and source.last_sync_status == "ok":
        # Jira expects "yyyy-MM-dd HH:mm" — crude slice from ISO
        ts = source.last_synced_at.replace("T", " ")[:16]
        jql_parts.append(f'updated >= "{ts}"')
    jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"

    issues = await jira_client.search(jql=jql, fields=_JIRA_FIELDS, max_results=100)

    new_count = 0
    for issue in issues:
        key = issue.get("key")
        if not key:
            continue
        fields = issue.get("fields") or {}

        # Comments: grab the last 3, flattened to text
        comment_container = fields.get("comment") or {}
        comments = comment_container.get("comments") or []
        recent_comments = comments[-3:] if len(comments) > 3 else comments
        comment_texts = [
            _text_from_adf(c.get("body"))[:400] for c in recent_comments
        ]

        status_name = ((fields.get("status") or {}).get("name")) or "Unknown"
        priority_name = ((fields.get("priority") or {}).get("name")) or "Unknown"
        issue_type = ((fields.get("issuetype") or {}).get("name")) or "Task"
        assignee = ((fields.get("assignee") or {}).get("displayName")) or "—"

        # Canonical fields for dedupe — a change in ANY of these bumps the hash
        canonical = {
            "status": status_name,
            "priority": priority_name,
            "summary": fields.get("summary"),
            "updated": fields.get("updated"),
            "comment_count": len(comments),
            "latest_comment_ids": [str(c.get("id")) for c in recent_comments],
        }
        payload_hash = _hash(canonical)

        seen = await _seen_hashes_for_ref(db, project_id, "jira", key)
        if payload_hash in seen:
            continue

        body_plain = _text_from_adf(fields.get("description"))[:2000]
        body_snippet = body_plain or "(keine Beschreibung)"
        if comment_texts:
            body_snippet += "\n\nLetzte Kommentare:\n" + "\n---\n".join(comment_texts)

        html_url = f"{jira_client._base_url}/browse/{key}" if jira_client._base_url else ""

        payload = {
            "title": fields.get("summary") or key,
            "state": status_name,
            "priority": priority_name,
            "issue_type": issue_type,
            "assignee": assignee,
            "updated_at": fields.get("updated") or "",
            "body_snippet": body_snippet[:3000],
            "html_url": html_url,
            "labels": fields.get("labels") or [],
            "comment_count": len(comments),
        }

        change = SourceChange(
            id=_gen_id(),
            project_id=project_id,
            source_link_id=source.id,
            source_type="jira",
            external_ref=key,
            payload_hash=payload_hash,
            title=f"{key} — {fields.get('summary') or ''}"[:500],
            analysis_status="pending",
        )
        change.payload = payload
        db.add(change)
        await db.flush()
        new_count += 1

    if new_count:
        await db.commit()
    return new_count


# --- git_repo adapter (S5) ---------------------------------------------------

COMMIT_BATCH_THRESHOLD = 10  # ≥ this many new commits → one batch row; else individual


def _format_range(commits: list[git_client.Commit]) -> str:
    if not commits:
        return ""
    newest = datetime.fromtimestamp(commits[0].timestamp, tz=timezone.utc)
    oldest = datetime.fromtimestamp(commits[-1].timestamp, tz=timezone.utc)
    if newest.date() == oldest.date():
        return newest.strftime("%Y-%m-%d")
    return f"{oldest.strftime('%Y-%m-%d')} … {newest.strftime('%Y-%m-%d')}"


def _top_authors(commits: list[git_client.Commit], limit: int = 3) -> str:
    counts: dict[str, int] = {}
    for c in commits:
        counts[c.author] = counts.get(c.author, 0) + 1
    top = sorted(counts.items(), key=lambda x: -x[1])[:limit]
    return ", ".join(f"{a} ({n})" for a, n in top) if top else "—"


@register("git_repo")
async def git_repo_adapter(
    db: AsyncSession,
    project_id: str,
    source: DataSourceLink,
) -> int:
    """Sync a local git repository.

    Config shape: { "path": "/abs/or/rel/path/to/repo", "branch": "main"? }

    Behavior:
    - First run (no prior sync): emit ONE `codebase_baseline` change
      with README + manifests + directory tree. Do NOT backfill commit history.
    - Subsequent runs: `git log --since <last_synced_at>`.
      - < COMMIT_BATCH_THRESHOLD new commits → one row per commit.
      - >= COMMIT_BATCH_THRESHOLD new commits → ONE `commit_batch` row.
    """
    cfg = source.config_dict
    repo_path = (cfg.get("path") or "").strip()
    if not repo_path:
        raise RuntimeError(f"git_repo source {source.id} missing path in config")
    branch = (cfg.get("branch") or "").strip() or None

    # --- Baseline on first run ---
    if not source.last_synced_at or source.last_sync_status != "ok":
        baseline = await git_client.build_baseline(repo_path)
        external_ref = f"baseline:{baseline['root_name']}"
        payload_hash = _hash({
            "head_sha": baseline["head_sha"],
            "readme_len": len(baseline["readme"] or ""),
            "manifest_names": sorted(baseline["manifests"].keys()),
            "tree_count": len(baseline["tree"]),
        })

        seen = await _seen_hashes_for_ref(db, project_id, "codebase_baseline", external_ref)
        if payload_hash in seen:
            return 0

        prompt_payload = git_client.format_baseline_prompt_payload(baseline)
        change = SourceChange(
            id=_gen_id(),
            project_id=project_id,
            source_link_id=source.id,
            source_type="codebase_baseline",
            external_ref=external_ref,
            payload_hash=payload_hash,
            title=f"Codebase-Baseline: {baseline['root_name']}"[:500],
            analysis_status="pending",
        )
        change.payload = {
            **prompt_payload,
            "html_url": "",
            "head_sha": baseline["head_sha"],
        }
        db.add(change)
        await db.flush()
        await db.commit()
        return 1

    # --- Delta: commits since last sync ---
    commits = await git_client.commits_since(
        repo_path,
        since_iso=source.last_synced_at,
        branch=branch,
        limit=500,
    )
    if not commits:
        return 0

    # Filter out already-seen SHAs
    existing_shas_res = await db.execute(
        select(SourceChange.external_ref).where(
            SourceChange.project_id == project_id,
            SourceChange.source_type.in_(("commit", "commit_batch")),
        )
    )
    existing_refs = {r[0] for r in existing_shas_res.all()}
    commits = [c for c in commits if c.sha not in existing_refs]
    if not commits:
        return 0

    if len(commits) >= COMMIT_BATCH_THRESHOLD:
        commit_list_lines = [
            f"- [{datetime.fromtimestamp(c.timestamp, tz=timezone.utc).strftime('%Y-%m-%d')}] "
            f"{c.sha[:8]} ({c.author}): {c.subject[:120]}"
            for c in commits[:80]
        ]
        if len(commits) > 80:
            commit_list_lines.append(f"… und {len(commits) - 80} weitere")
        external_ref = f"batch:{commits[0].sha[:12]}…{commits[-1].sha[:12]}"
        payload_hash = _hash({
            "count": len(commits),
            "first_sha": commits[0].sha,
            "last_sha": commits[-1].sha,
        })

        change = SourceChange(
            id=_gen_id(),
            project_id=project_id,
            source_link_id=source.id,
            source_type="commit_batch",
            external_ref=external_ref,
            payload_hash=payload_hash,
            title=f"{len(commits)} Commits ({_format_range(commits)})"[:500],
            analysis_status="pending",
        )
        change.payload = {
            "date_range": _format_range(commits),
            "commit_count": len(commits),
            "top_authors": _top_authors(commits),
            "commit_list": "\n".join(commit_list_lines),
            "html_url": "",
        }
        db.add(change)
        await db.flush()
        await db.commit()
        return 1

    # Individual commits — enrich each with numstat
    new_count = 0
    for c in commits:
        try:
            await git_client.enrich_with_stats(repo_path, c)
        except Exception:
            pass

        canonical = {"sha": c.sha, "subject": c.subject, "additions": c.additions, "deletions": c.deletions}
        payload_hash = _hash(canonical)

        top_files = c.top_files or []
        payload = {
            "title": c.subject or c.sha[:8],
            "author": c.author,
            "message": (c.subject + ("\n\n" + c.body if c.body else ""))[:2000],
            "additions": c.additions,
            "deletions": c.deletions,
            "changed_files": c.files_changed,
            "top_files": "\n".join(f"- {f}" for f in top_files[:10]) or "(keine)",
            "html_url": "",
            "sha": c.sha,
        }

        change = SourceChange(
            id=_gen_id(),
            project_id=project_id,
            source_link_id=source.id,
            source_type="commit",
            external_ref=c.sha,
            payload_hash=payload_hash,
            title=(c.subject or c.sha[:8])[:500],
            analysis_status="pending",
        )
        change.payload = payload
        db.add(change)
        await db.flush()
        new_count += 1

    if new_count:
        await db.commit()
    return new_count
