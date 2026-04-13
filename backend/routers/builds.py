from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.project import DataSourceLink
from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/builds", tags=["builds"])


class BuildTriggerRequest(BaseModel):
    job_name: str
    path_name: str | None = None
    parameters: dict = {}


@router.get("")
async def list_all_builds(db: AsyncSession = Depends(get_db)):
    """Get build status for all linked Jenkins jobs."""
    result = await db.execute(
        select(DataSourceLink).where(DataSourceLink.source_type == "jenkins_job")
    )
    links = result.scalars().all()

    builds = []
    seen_paths = set()
    for link in links:
        import json
        config = json.loads(link.source_config) if link.source_config else {}
        path_name = config.get("path_name", "")
        if path_name in seen_paths:
            continue
        seen_paths.add(path_name)

        data = await ai_assist.get_jenkins_jobs(path_name or None)
        if data and "jobs" in data:
            for job in data["jobs"]:
                builds.append({
                    "project_id": link.project_id,
                    "source_id": link.id,
                    "display_name": link.display_name,
                    "path_name": path_name,
                    "job_name": job.get("name", ""),
                    "url": job.get("url", ""),
                    "color": job.get("color", "notbuilt"),
                    "last_build": job.get("lastBuild"),
                })

    return {"builds": builds, "connected": ai_assist.is_connected}


@router.get("/{project_id}")
async def project_builds(project_id: str, db: AsyncSession = Depends(get_db)):
    """Get build status for a specific project's Jenkins jobs."""
    result = await db.execute(
        select(DataSourceLink).where(
            DataSourceLink.project_id == project_id,
            DataSourceLink.source_type == "jenkins_job",
        )
    )
    links = result.scalars().all()

    builds = []
    for link in links:
        import json
        config = json.loads(link.source_config) if link.source_config else {}
        path_name = config.get("path_name", "")
        job_name_filter = config.get("job_name", "")

        data = await ai_assist.get_jenkins_jobs(path_name or None)
        if data and "jobs" in data:
            for job in data["jobs"]:
                if job_name_filter and job.get("name") != job_name_filter:
                    continue
                builds.append({
                    "project_id": project_id,
                    "source_id": link.id,
                    "display_name": link.display_name,
                    "path_name": path_name,
                    "job_name": job.get("name", ""),
                    "url": job.get("url", ""),
                    "color": job.get("color", "notbuilt"),
                    "last_build": job.get("lastBuild"),
                })

    return {"builds": builds, "connected": ai_assist.is_connected}


@router.post("/trigger")
async def trigger_build(data: BuildTriggerRequest):
    """Trigger a Jenkins build via AI-Assist."""
    result = await ai_assist.post("/api/jenkins/build", {
        "job_name": data.job_name,
        "path_name": data.path_name,
        "parameters": data.parameters,
    })
    if result is None:
        raise HTTPException(503, "AI-Assist nicht erreichbar")
    return result
