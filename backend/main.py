import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import settings
from database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("projecthub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("ProjectHub startet auf Port %s", settings.port)

    # Ensure data directory exists
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialize database
    import models  # noqa: F401 — register all models before create_all
    await init_db()
    logger.info("Datenbank initialisiert: %s", settings.db_path)

    # Start polling service
    from services.polling_service import start_polling, stop_polling
    start_polling()

    yield

    # Shutdown
    stop_polling()
    from services.ai_assist_client import ai_assist
    from services.jira_client import jira_client
    await ai_assist.close()
    await jira_client.close()
    logger.info("ProjectHub wird beendet")


app = FastAPI(
    title="ProjectHub",
    description="Projekt- und Aufgabenverwaltung mit AI-Assist Integration",
    version="1.9.0",
    lifespan=lifespan,
)

# CORS for Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register Routers ---
from routers.projects import router as projects_router  # noqa: E402
from routers.todos import router as todos_router  # noqa: E402
from routers.notes import router as notes_router  # noqa: E402
from routers.dashboard import router as dashboard_router  # noqa: E402
from routers.events import router as events_router  # noqa: E402
from routers.builds import router as builds_router  # noqa: E402
from routers.pulls import router as pulls_router  # noqa: E402
from routers.settings import router as settings_router  # noqa: E402
from routers.inbox import router as inbox_router  # noqa: E402
from routers.todo_queue import router as todo_queue_router  # noqa: E402
from routers.chat import router as chat_router  # noqa: E402
from routers.search import router as search_router  # noqa: E402
from routers.activity import router as activity_router  # noqa: E402
from routers.knowledge import router as knowledge_router  # noqa: E402
from routers.synapse import router as synapse_router  # noqa: E402
from routers.sync import router as sync_router  # noqa: E402
from routers.project_sync import router as project_sync_router  # noqa: E402
from routers.update import router as update_router  # noqa: E402
from routers.ai import router as ai_router  # noqa: E402
from routers.memory import router as memory_router  # noqa: E402

app.include_router(projects_router)
app.include_router(todos_router)
app.include_router(notes_router)
app.include_router(dashboard_router)
app.include_router(events_router)
app.include_router(builds_router)
app.include_router(pulls_router)
app.include_router(settings_router)
app.include_router(inbox_router)
app.include_router(todo_queue_router)
app.include_router(chat_router)
app.include_router(search_router)
app.include_router(activity_router)
app.include_router(sync_router)
app.include_router(project_sync_router)
app.include_router(knowledge_router)
app.include_router(synapse_router)
app.include_router(update_router)
app.include_router(ai_router)
app.include_router(memory_router)


# Health check
@app.get("/api/health")
async def health():
    from services.update_service import get_current_version
    return {"status": "ok", "version": get_current_version()}


# Serve frontend in production (when dist/ exists).
# Nicht-API-Pfade fallen auf index.html zurück, damit React Router Client-Routes
# wie /timeline oder /projekte/:id auch bei Direkt-Aufruf / F5 funktionieren.
class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        url_path = scope.get("path", "") if isinstance(scope, dict) else ""
        if url_path.startswith("/api/") or url_path == "/api":
            raise StarletteHTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            if ex.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", SPAStaticFiles(directory=str(frontend_dist)), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        reload=False,
        access_log=True,
        log_level="info",
    )
