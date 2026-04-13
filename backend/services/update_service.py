"""
Update Service - GitHub-basierte App-Updates für ProjectHub.

Ermöglicht das Herunterladen und Installieren von Updates aus einem
GitHub-Repository via ZIP-Download mit Whitelist-Filterung.

Features:
- Version-Check via GitHub API (Releases, Tags oder Branch)
- ZIP-Download mit optionalem Proxy-Support
- Whitelist-basierte Extraktion (nur Code, keine Configs/Daten)
- Backup vor Update
- Server-Restart nach Update
"""

import asyncio
import base64
import fnmatch
import io
import logging
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("projecthub.update")

# Pfade
VERSION_FILE = Path(__file__).parent.parent.parent / "VERSION"
APP_ROOT = Path(__file__).parent.parent.parent
CURRENT_VERSION = "1.0.0"  # Fallback

# Update-Konfiguration (kann über config.py erweitert werden)
UPDATE_CONFIG = {
    "enabled": True,
    "repo_url": "",  # z.B. "Markus-Dev96/ProjectHub"
    "branch": "main",
    "github_token": "",
    "timeout_seconds": 60,
    "verify_ssl": True,
    # Whitelist: nur diese Dateien/Ordner werden beim Update überschrieben
    "include_patterns": [
        "backend/**/*.py",
        "backend/requirements.txt",
        "frontend/src/**",
        "frontend/package.json",
        "frontend/vite.config.ts",
        "frontend/tsconfig*.json",
        "frontend/index.html",
        "VERSION",
    ],
    # Blacklist: diese Dateien werden nie überschrieben
    "exclude_patterns": [
        "backend/data/**",
        "backend/*.db",
        "backend/.env",
        "frontend/node_modules/**",
        "frontend/dist/**",
        "**/__pycache__/**",
        "**/.git/**",
    ],
}


def get_current_version() -> str:
    """Liest die aktuelle Version aus VERSION-Datei oder Fallback."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return CURRENT_VERSION


def parse_repo_url(repo_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Parst GitHub-Repository-URL in Owner und Repo-Name."""
    url = repo_url.strip().rstrip("/")
    if url.startswith("https://"):
        url = url[8:]
    elif url.startswith("http://"):
        url = url[7:]
    if url.startswith("github.com/"):
        url = url[11:]

    parts = url.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None, None


def matches_pattern(path: str, patterns: List[str]) -> bool:
    """Prüft ob ein Pfad zu einem der Glob-Patterns passt."""
    path = path.replace("\\", "/")
    for pattern in patterns:
        pattern = pattern.replace("\\", "/")
        if "**" in pattern:
            regex_pattern = pattern
            regex_pattern = regex_pattern.replace(".", "\\.")
            regex_pattern = regex_pattern.replace("**/", "\x00STARSTAR_SLASH\x00")
            regex_pattern = regex_pattern.replace("/**", "\x00SLASH_STARSTAR\x00")
            regex_pattern = regex_pattern.replace("**", "\x00STARSTAR\x00")
            regex_pattern = regex_pattern.replace("*", "[^/]*")
            regex_pattern = regex_pattern.replace("\x00STARSTAR_SLASH\x00", "(?:.+/)?")
            regex_pattern = regex_pattern.replace("\x00SLASH_STARSTAR\x00", "(?:/.*)?")
            regex_pattern = regex_pattern.replace("\x00STARSTAR\x00", ".*")
            regex_pattern = f"^{regex_pattern}$"
            if re.match(regex_pattern, path):
                return True
        elif fnmatch.fnmatch(path, pattern):
            return True
    return False


def should_extract_file(relative_path: str) -> bool:
    """Prüft ob eine Datei extrahiert werden soll."""
    if matches_pattern(relative_path, UPDATE_CONFIG["exclude_patterns"]):
        return False
    return matches_pattern(relative_path, UPDATE_CONFIG["include_patterns"])


class UpdateService:
    """Service für GitHub-basierte App-Updates."""

    def __init__(self):
        self.app_root = APP_ROOT
        self.backup_dir = self.app_root / "backups" / "updates"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _get_http_client(self) -> httpx.AsyncClient:
        """Erstellt HTTP-Client."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ProjectHub-Update-Service/1.0",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
        token = UPDATE_CONFIG.get("github_token")
        if token:
            headers["Authorization"] = f"token {token}"

        return httpx.AsyncClient(
            timeout=httpx.Timeout(UPDATE_CONFIG["timeout_seconds"]),
            verify=UPDATE_CONFIG["verify_ssl"],
            headers=headers,
            follow_redirects=True,
        )

    def _add_cache_bust(self, url: str) -> str:
        """Fügt Cache-Busting Query-Parameter hinzu."""
        import time
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}_nocache={int(time.time() * 1000)}"

    async def check_for_updates(self) -> Dict:
        """Prüft auf verfügbare Updates."""
        current = get_current_version()

        repo_url = UPDATE_CONFIG.get("repo_url", "")
        if not UPDATE_CONFIG.get("enabled") or not repo_url:
            return {
                "available": False,
                "current_version": current,
                "error": "Update-Service nicht konfiguriert",
            }

        owner, repo = parse_repo_url(repo_url)
        if not owner or not repo:
            return {
                "available": False,
                "current_version": current,
                "error": f"Ungültige Repository-URL: {repo_url}",
            }

        try:
            async with self._get_http_client() as client:
                branch = UPDATE_CONFIG.get("branch", "main")

                # VERSION-Datei via Contents API lesen
                version_url = self._add_cache_bust(
                    f"https://api.github.com/repos/{owner}/{repo}/contents/VERSION?ref={branch}"
                )
                version_response = await client.get(version_url)

                if version_response.status_code == 200:
                    version_data = version_response.json()
                    content_b64 = version_data.get("content", "")
                    latest = base64.b64decode(content_b64).decode("utf-8").strip()
                else:
                    # Fallback: Branch Commit Info
                    branch_url = self._add_cache_bust(
                        f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
                    )
                    branch_response = await client.get(branch_url)
                    if branch_response.status_code == 200:
                        commit_sha = branch_response.json().get("commit", {}).get("sha", "")[:7]
                        latest = f"{branch}@{commit_sha}"
                    else:
                        return {
                            "available": False,
                            "current_version": current,
                            "error": f"Branch '{branch}' nicht erreichbar",
                        }

                # Commit Info für Release Notes
                branch_url = self._add_cache_bust(
                    f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
                )
                branch_response = await client.get(branch_url)
                commit_info = ""
                if branch_response.status_code == 200:
                    commit = branch_response.json().get("commit", {}).get("commit", {})
                    commit_info = commit.get("message", "").split("\n")[0]

                available = latest != current
                logger.info(f"[update] Version check: current={current}, remote={latest}, available={available}")

                return {
                    "available": available,
                    "current_version": current,
                    "latest_version": latest,
                    "release_notes": commit_info,
                    "download_url": f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}",
                }

        except httpx.TimeoutException:
            return {"available": False, "current_version": current, "error": "Timeout bei GitHub-Verbindung"}
        except Exception as e:
            logger.exception("[update] Check failed")
            return {"available": False, "current_version": current, "error": str(e)}

    async def download_and_install(
        self,
        download_url: Optional[str] = None,
        create_backup: bool = True,
        progress_callback: Optional[callable] = None,
    ) -> Dict:
        """Lädt Update herunter und installiert es."""
        if not UPDATE_CONFIG.get("enabled"):
            return {"success": False, "error": "Update-Service nicht aktiviert"}

        async def report(stage: str, percent: int, message: str):
            if progress_callback:
                await progress_callback(stage, percent, message)
            logger.info(f"[update] {stage}: {percent}% - {message}")

        try:
            await report("prepare", 0, "Ermittle Download-URL...")

            if not download_url:
                check = await self.check_for_updates()
                if "error" in check:
                    return {"success": False, "error": check["error"]}
                download_url = check.get("download_url")

            if not download_url:
                return {"success": False, "error": "Keine Download-URL"}

            await report("download", 10, "Lade Update herunter...")

            async with self._get_http_client() as client:
                response = await client.get(download_url)
                if response.status_code != 200:
                    return {"success": False, "error": f"Download fehlgeschlagen: HTTP {response.status_code}"}
                zip_data = response.content

            await report("download", 50, f"Download OK ({len(zip_data) // 1024} KB)")

            await report("analyze", 55, "Analysiere Update-Paket...")

            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, "r") as zf:
                all_files = zf.namelist()
                if not all_files:
                    return {"success": False, "error": "Leeres ZIP-Archiv"}

                root_prefix = all_files[0].split("/")[0] + "/"
                files_to_extract = []
                for zip_path in all_files:
                    if zip_path.endswith("/"):
                        continue
                    relative_path = zip_path[len(root_prefix):]
                    if relative_path and should_extract_file(relative_path):
                        files_to_extract.append((zip_path, relative_path))

                if not files_to_extract:
                    return {"success": False, "error": "Keine aktualisierbaren Dateien gefunden"}

                await report("analyze", 60, f"{len(files_to_extract)} Dateien zu aktualisieren")

                # Backup
                backup_path = None
                if create_backup:
                    await report("backup", 65, "Erstelle Backup...")
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.backup_dir / f"backup_{timestamp}"
                    backup_path.mkdir(parents=True, exist_ok=True)

                    for _, relative_path in files_to_extract:
                        source = self.app_root / relative_path
                        if source.exists():
                            dest = backup_path / relative_path
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, dest)

                    await report("backup", 75, f"Backup: {backup_path.name}")

                # Install
                await report("install", 80, "Installiere Update...")
                updated_files = []
                for i, (zip_path, relative_path) in enumerate(files_to_extract):
                    dest = self.app_root / relative_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(zip_path) as source:
                        with open(dest, "wb") as target:
                            target.write(source.read())
                    updated_files.append(relative_path)

            await report("complete", 100, f"{len(updated_files)} Dateien aktualisiert")

            return {
                "success": True,
                "message": f"{len(updated_files)} Dateien aktualisiert",
                "files_updated": updated_files,
                "backup_path": str(backup_path) if backup_path else None,
                "restart_required": True,
            }

        except zipfile.BadZipFile:
            return {"success": False, "error": "Ungültiges ZIP-Archiv"}
        except Exception as e:
            logger.exception("[update] Installation failed")
            return {"success": False, "error": str(e)}

    async def restore_backup(self, backup_name: str) -> Dict:
        """Stellt ein Backup wieder her."""
        backup_path = self.backup_dir / backup_name
        if not backup_path.exists():
            return {"success": False, "error": f"Backup nicht gefunden: {backup_name}"}

        try:
            restored = []
            for f in backup_path.rglob("*"):
                if f.is_file():
                    relative = f.relative_to(backup_path)
                    dest = self.app_root / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    restored.append(str(relative))
            return {"success": True, "message": f"{len(restored)} Dateien wiederhergestellt", "restart_required": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_backups(self) -> List[Dict]:
        """Listet alle verfügbaren Backups."""
        backups = []
        if not self.backup_dir.exists():
            return backups
        for d in sorted(self.backup_dir.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith("backup_"):
                file_count = sum(1 for _ in d.rglob("*") if _.is_file())
                backups.append({"name": d.name, "created": d.stat().st_mtime, "file_count": file_count})
        return backups

    def request_restart(self) -> None:
        """Fordert einen Server-Neustart an."""
        import subprocess
        import platform

        logger.info("[update] Server-Neustart angefordert...")
        python = sys.executable
        main_script = str(self.app_root / "backend" / "main.py")

        if platform.system() == "Windows":
            DETACHED_PROCESS = 0x00000008
            subprocess.Popen(
                [python, main_script],
                creationflags=DETACHED_PROCESS,
                close_fds=True,
                cwd=str(self.app_root / "backend"),
            )
            os._exit(0)
        else:
            os.execv(python, [python, main_script])


# Singleton
_update_service: Optional[UpdateService] = None


def get_update_service() -> UpdateService:
    global _update_service
    if _update_service is None:
        _update_service = UpdateService()
    return _update_service
