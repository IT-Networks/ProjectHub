from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # AI-Assist Backend
    ai_assist_url: str = "http://localhost:8000"
    ai_assist_timeout: int = 30
    ai_assist_llm_timeout: int = 120

    # Database
    db_path: str = str(Path(__file__).parent / "data" / "projecthub.db")

    # Polling
    polling_interval_minutes: int = 5
    polling_enabled: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 3001
    cors_origins: list[str] = ["http://localhost:3001", "http://localhost:3000", "http://127.0.0.1:3000"]

    # UI
    theme: str = "dark"
    language: str = "de"
    kanban_columns: list[str] = ["backlog", "in_progress", "review", "done"]

    model_config = {"env_prefix": "PROJECTHUB_"}


settings = Settings()
