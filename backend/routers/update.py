import logging
from fastapi import APIRouter
from pydantic import BaseModel
from services.update_service import get_update_service, get_current_version

router = APIRouter(prefix="/api/update", tags=["update"])
logger = logging.getLogger("projecthub.update")


@router.get("/version")
async def get_version():
    """Gibt aktuelle Version zurück."""
    return {"version": get_current_version()}


@router.get("/check")
async def check_updates():
    """Prüft auf verfügbare Updates."""
    service = get_update_service()
    return await service.check_for_updates()


class InstallRequest(BaseModel):
    download_url: str | None = None
    create_backup: bool = True


@router.post("/install")
async def install_update(data: InstallRequest):
    """Lädt Update herunter und installiert es."""
    service = get_update_service()
    result = await service.download_and_install(
        download_url=data.download_url,
        create_backup=data.create_backup,
    )
    return result


@router.get("/backups")
async def list_backups():
    """Listet verfügbare Backups."""
    service = get_update_service()
    return {"backups": service.list_backups()}


class RestoreRequest(BaseModel):
    backup_name: str


@router.post("/restore")
async def restore_backup(data: RestoreRequest):
    """Stellt ein Backup wieder her."""
    service = get_update_service()
    return await service.restore_backup(data.backup_name)


@router.post("/restart")
async def restart_server():
    """Startet den Server neu."""
    service = get_update_service()
    service.request_restart()
    return {"message": "Neustart wird durchgeführt..."}
