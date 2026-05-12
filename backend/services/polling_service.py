import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func

from config import settings
from database import async_session
from models.project import DataSourceLink
from services.ai_assist_client import ai_assist
from services.sse_hub import sse_hub

logger = logging.getLogger("projecthub.polling")

_polling_task: asyncio.Task | None = None
_periodic_sync_task: asyncio.Task | None = None

# 30 min cadence for per-project sync (matches AUTO_SYNC_COOLDOWN_SECONDS)
PERIODIC_SYNC_INTERVAL_S = 30 * 60
# Stagger gap between projects so we don't saturate AI-Assist
PER_PROJECT_STAGGER_S = 2


async def _poll_cycle():
    """Single polling cycle: fetch Jenkins builds, GitHub repos, connectivity."""
    logger.debug("Polling-Zyklus gestartet")

    # 1. Jenkins builds
    try:
        async with async_session() as db:
            result = await db.execute(
                select(DataSourceLink).where(DataSourceLink.source_type == "jenkins_job")
            )
            jenkins_links = result.scalars().all()

        seen_paths = set()
        for link in jenkins_links:
            config = json.loads(link.source_config) if link.source_config else {}
            path_name = config.get("path_name", "")
            if path_name in seen_paths:
                continue
            seen_paths.add(path_name)

            data = await ai_assist.get_jenkins_jobs(path_name or None)
            if data and "jobs" in data:
                await sse_hub.emit("build_update", {
                    "path_name": path_name,
                    "jobs": data["jobs"],
                    "job_count": data.get("job_count", len(data["jobs"])),
                })
    except Exception as e:
        logger.error("Jenkins-Polling fehlgeschlagen: %s", e)

    # 2. GitHub repos
    try:
        async with async_session() as db:
            result = await db.execute(
                select(DataSourceLink).where(DataSourceLink.source_type == "github_repo")
            )
            github_links = result.scalars().all()

        seen_orgs = set()
        for link in github_links:
            config = json.loads(link.source_config) if link.source_config else {}
            owner = config.get("owner", "")
            if not owner or owner in seen_orgs:
                continue
            seen_orgs.add(owner)

            data = await ai_assist.get(
                "/api/github/repos",
                params={"org": owner},
                cache_key=f"github:repos:{owner}",
                cache_type="github_repos",
            )
            if data and "repos" in data:
                await sse_hub.emit("pr_update", {
                    "org": owner,
                    "repos": data["repos"],
                })
    except Exception as e:
        logger.error("GitHub-Polling fehlgeschlagen: %s", e)

    # 3. Email todos → Queue
    try:
        from models.todo import TodoQueue
        import secrets

        data = await ai_assist.get_email_todos(status="new")
        if data and "todos" in data:
            async with async_session() as db:
                for todo in data["todos"]:
                    source_ref = todo.get("email_id", todo.get("id", ""))
                    source = todo.get("source", "email")

                    # Duplicate check
                    existing = await db.execute(
                        select(TodoQueue).where(
                            TodoQueue.source == source,
                            TodoQueue.source_ref == source_ref,
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    queue_item = TodoQueue(
                        id=secrets.token_hex(8),
                        suggested_title=todo.get("todo_text", todo.get("subject", ""))[:500],
                        suggested_description=todo.get("ai_analysis", ""),
                        suggested_priority=todo.get("priority", "medium"),
                        suggested_deadline=todo.get("deadline"),
                        source=source,
                        source_ref=source_ref,
                        source_subject=todo.get("subject", ""),
                        source_sender=todo.get("sender", ""),
                        source_date=todo.get("received_at", ""),
                        source_snapshot=json.dumps(todo.get("mail_snapshot", {})),
                        ai_analysis=todo.get("ai_analysis", ""),
                        ai_confidence=0.7,
                    )
                    db.add(queue_item)
                    await sse_hub.emit("queue_item", {
                        "id": queue_item.id,
                        "title": queue_item.suggested_title,
                        "source": queue_item.source,
                        "sender": queue_item.source_sender,
                    })

                await db.commit()
                logger.info("Email-Todos geprüft: %d neue", len(data["todos"]))
    except Exception as e:
        logger.error("Email-Todo-Polling fehlgeschlagen: %s", e)

    # 4. Connectivity status
    await sse_hub.emit("ai_assist_status", {
        "connected": ai_assist.is_connected,
    })


async def _polling_loop():
    """Background loop that runs poll cycles at the configured interval."""
    interval = settings.polling_interval_minutes * 60

    # Initial delay to let AI-Assist start up
    await asyncio.sleep(5)

    # Initial health check
    await ai_assist.health_check()
    await sse_hub.emit("ai_assist_status", {"connected": ai_assist.is_connected})

    # First poll immediately
    await _poll_cycle()

    while True:
        await asyncio.sleep(interval)
        try:
            await _poll_cycle()
        except Exception as e:
            logger.error("Polling-Fehler: %s", e)


async def _sync_projects_with_sources() -> int:
    """Trigger a sync run for every project that has at least one enabled source.

    Runs sequentially with a small stagger so adapter I/O doesn't pile up.
    Each per-project call re-uses `trigger_sync` semantics: cooldowns apply,
    in-progress runs are skipped.
    """
    # Local import avoids circular deps (project_sync imports ai_assist too)
    from routers.project_sync import _run_sync_for_project, _get_running_run, _get_last_run, _parse_iso, AUTO_SYNC_COOLDOWN_SECONDS
    from models.source_change import SyncRun
    import secrets

    triggered = 0
    async with async_session() as db:
        # Distinct project_ids that have at least one enabled source
        stmt = (
            select(DataSourceLink.project_id)
            .where(DataSourceLink.sync_enabled == 1)
            .group_by(DataSourceLink.project_id)
        )
        project_ids = [row[0] for row in (await db.execute(stmt)).all()]

    for pid in project_ids:
        # Respect cooldown — same logic as the HTTP trigger endpoint
        async with async_session() as db:
            running = await _get_running_run(db, pid)
            if running:
                continue
            last = await _get_last_run(db, pid)
            if last and last.started_at:
                dt = _parse_iso(last.started_at)
                if dt:
                    elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
                    if elapsed < AUTO_SYNC_COOLDOWN_SECONDS:
                        continue
            # Create run row
            run = SyncRun(
                id=secrets.token_hex(8),
                project_id=pid,
                trigger="periodic",
                status="running",
            )
            db.add(run)
            await db.commit()
            run_id = run.id

        # Kick off as a separate task so slow runs don't block others
        asyncio.create_task(_run_sync_for_project(pid, "periodic", run_id))
        triggered += 1
        await asyncio.sleep(PER_PROJECT_STAGGER_S)

    if triggered:
        logger.info("Periodischer Sync gestartet für %d Projekt(e)", triggered)
    return triggered


async def _periodic_sync_loop():
    """Background loop: every 30 min, trigger sync for all active projects."""
    # Delay first run so the regular polling_loop goes first at startup
    await asyncio.sleep(60)
    while True:
        try:
            await _sync_projects_with_sources()
        except Exception as e:
            logger.error("Periodischer Sync fehlgeschlagen: %s", e)
        await asyncio.sleep(PERIODIC_SYNC_INTERVAL_S)


def start_polling():
    """Start the background polling + periodic-sync tasks."""
    global _polling_task, _periodic_sync_task
    if not settings.polling_enabled:
        logger.info("Polling ist deaktiviert")
        return
    if _polling_task and not _polling_task.done():
        logger.warning("Polling läuft bereits")
        return
    _polling_task = asyncio.create_task(_polling_loop())
    _periodic_sync_task = asyncio.create_task(_periodic_sync_loop())
    logger.info(
        "Polling gestartet (Status-Intervall: %d Min., Projekt-Sync: %d Min.)",
        settings.polling_interval_minutes, PERIODIC_SYNC_INTERVAL_S // 60,
    )


def stop_polling():
    """Stop background tasks."""
    global _polling_task, _periodic_sync_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        logger.info("Polling gestoppt")
    _polling_task = None
    if _periodic_sync_task and not _periodic_sync_task.done():
        _periodic_sync_task.cancel()
        logger.info("Periodischer Sync gestoppt")
    _periodic_sync_task = None
