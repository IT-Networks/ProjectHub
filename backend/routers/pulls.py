import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.project import DataSourceLink
from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/pulls", tags=["pulls"])


@router.get("")
async def list_all_prs(db: AsyncSession = Depends(get_db)):
    """Get open PRs from all linked GitHub repos."""
    result = await db.execute(
        select(DataSourceLink).where(DataSourceLink.source_type == "github_repo")
    )
    links = result.scalars().all()

    all_prs = []
    for link in links:
        config = json.loads(link.source_config) if link.source_config else {}
        owner = config.get("owner", "")
        repo = config.get("repo", "")
        if not owner or not repo:
            continue

        # Get repo info from AI-Assist
        data = await ai_assist.get(
            f"/api/github/repos",
            params={"org": owner},
            cache_key=f"github:repos:{owner}",
            cache_type="github_repos",
        )
        if data and "repos" in data:
            for r in data["repos"]:
                if r.get("name") == repo or r.get("full_name") == f"{owner}/{repo}":
                    all_prs.append({
                        "project_id": link.project_id,
                        "source_id": link.id,
                        "owner": owner,
                        "repo": repo,
                        "display_name": link.display_name or f"{owner}/{repo}",
                        "open_issues_count": r.get("open_issues_count", 0),
                        "default_branch": r.get("default_branch", "main"),
                        "updated_at": r.get("updated_at", ""),
                    })

    return {"repos": all_prs, "connected": ai_assist.is_connected}


@router.get("/{project_id}")
async def project_prs(project_id: str, db: AsyncSession = Depends(get_db)):
    """Get PRs for a specific project's GitHub repos."""
    result = await db.execute(
        select(DataSourceLink).where(
            DataSourceLink.project_id == project_id,
            DataSourceLink.source_type == "github_repo",
        )
    )
    links = result.scalars().all()

    repos = []
    for link in links:
        config = json.loads(link.source_config) if link.source_config else {}
        owner = config.get("owner", "")
        repo = config.get("repo", "")
        if not owner or not repo:
            continue
        repos.append({
            "project_id": project_id,
            "source_id": link.id,
            "owner": owner,
            "repo": repo,
            "display_name": link.display_name or f"{owner}/{repo}",
        })

    return {"repos": repos, "connected": ai_assist.is_connected}


@router.get("/detail/{owner}/{repo}/{pr_number}")
async def pr_detail(owner: str, repo: str, pr_number: int):
    """Get PR details via AI-Assist proxy."""
    data = await ai_assist.get_pr_details(owner, repo, pr_number)
    if data is None:
        raise HTTPException(503, "AI-Assist nicht erreichbar")
    return data


@router.get("/diff/{owner}/{repo}/{pr_number}")
async def pr_diff(owner: str, repo: str, pr_number: int):
    """Get PR diff via AI-Assist proxy."""
    data = await ai_assist.get_pr_diff(owner, repo, pr_number)
    if data is None:
        raise HTTPException(503, "AI-Assist nicht erreichbar")
    return data


@router.post("/review/{owner}/{repo}/{pr_number}")
async def review_pr(owner: str, repo: str, pr_number: int):
    """Trigger LLM-based PR review via AI-Assist."""
    data = await ai_assist.analyze_pr(owner, repo, pr_number)
    if data is None:
        raise HTTPException(503, "AI-Assist nicht erreichbar")
    return data
