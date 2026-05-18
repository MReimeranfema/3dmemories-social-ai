"""
Google Drive Upload-Client.
Spezifikation: api/api-gateway.md → API 3: Google Drive API

Service Account Auth — kein OAuth-Flow, keine User-Credentials.

Upload-Strategie (laut Spezifikation):
  ≤ 5 MB  → Simple Upload   (MediaIoBaseUpload, resumable=False)
  > 5 MB  → Resumable Upload (resumable=True, chunksize=5 MB)

Integrität: SHA-256 lokal berechnen + MD5 aus Drive-API vergleichen.
  (Drive liefert MD5; lokal beide Hashes für Audit-Trail)

Soft Delete: Keine files.delete — Rename zu "DELETED_<timestamp>_<name>".

Dev-Mode: Wenn GOOGLE_SERVICE_ACCOUNT_JSON leer → kein API-Call, nur loggen.
Alle Fehler werden geloggt und weitergeworfen — Caller entscheidet ob Retry.
"""

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

# Upload-Grenze Simple vs. Resumable (5 MB in Bytes)
SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024  # 5 MB


# ── Exceptions ─────────────────────────────────────────────────────────────────

class DriveUploadError(Exception):
    """Fehler beim Upload zu Google Drive."""

class DriveIntegrityError(DriveUploadError):
    """Hash-Prüfung nach Upload fehlgeschlagen."""

class DriveAuthError(DriveUploadError):
    """Service-Account-Authentifizierung fehlgeschlagen."""


# ── DriveClient ────────────────────────────────────────────────────────────────

class DriveClient:
    """
    Google Drive API Wrapper — Service Account.

    Singleton-Nutzung empfohlen (einmal instanziieren, überall übergeben).
    Thread-safe für lesende Operationen; Upload-Calls sind sequenziell sicher
    da GDrive-API zustandslos ist (kein geteilter Mutable State).

    Im Dev-Mode (GOOGLE_SERVICE_ACCOUNT_JSON leer):
      Alle Methoden loggen was sie tun würden und geben Dummy-Werte zurück.
      Keine echten API-Calls, kein Import von google-Bibliotheken nötig.
    """

    def __init__(self) -> None:
        self._service: Any = None          # googleapiclient.discovery.Resource
        self._dev_mode = (
            not settings.GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT
            and not settings.GOOGLE_SERVICE_ACCOUNT_JSON
        )

        if self._dev_mode:
            log.warning(
                "drive_client.dev_mode",
                reason="Keine Google-Credentials gesetzt — alle Uploads simuliert",
            )
        else:
            self._service = self._build_service()

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def upload_text_file(
        self,
        filename: str,
        content: str,
        folder_id: str | None = None,
        mime_type: str = "text/plain",
    ) -> tuple[str, str]:
        """
        Lädt einen Text-String als Datei hoch.

        Args:
            filename:  Dateiname in Drive (z.B. "caption_IG-2026-042.txt")
            content:   Dateiinhalt als String (UTF-8)
            folder_id: Ziel-Ordner-ID; fallback auf GOOGLE_DRIVE_FOLDER_ID
            mime_type: MIME-Typ der Datei

        Returns:
            (file_id, webViewLink) — Drive-Datei-ID und Browser-URL

        Raises:
            DriveUploadError: API-Fehler oder Netzwerkproblem
        """
        encoded = content.encode("utf-8")
        size_bytes = len(encoded)

        if self._dev_mode:
            sha256 = hashlib.sha256(encoded).hexdigest()
            file_id = f"DUMMY_FILE_{filename.replace('.', '_').upper()}"
            web_link = f"https://drive.google.com/file/d/{file_id}/view"
            log.info(
                "drive.upload_text_file.simulated",
                filename=filename,
                size_bytes=size_bytes,
                sha256=sha256[:16] + "...",
                file_id=file_id,
            )
            return file_id, web_link

        fh = io.BytesIO(encoded)
        return self._upload_stream(
            fh,
            filename=filename,
            size_bytes=size_bytes,
            mime_type=mime_type,
            folder_id=folder_id or settings.GOOGLE_DRIVE_FOLDER_ID,
            original_bytes=encoded,
        )

    def upload_file(
        self,
        filename: str,
        filepath: str | Path,
        folder_id: str | None = None,
        mime_type: str = "application/octet-stream",
    ) -> tuple[str, str]:
        """
        Lädt eine lokale Datei zu Google Drive hoch.

        Args:
            filename:  Dateiname in Drive
            filepath:  Lokaler Pfad zur Datei
            folder_id: Ziel-Ordner-ID; fallback auf GOOGLE_DRIVE_FOLDER_ID
            mime_type: MIME-Typ; für Bilder z.B. "image/jpeg" oder "image/png"

        Returns:
            (file_id, webViewLink)

        Raises:
            FileNotFoundError:   Lokale Datei nicht gefunden
            DriveUploadError:    API-Fehler
            DriveIntegrityError: Hash-Mismatch nach Upload
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {filepath}")

        size_bytes = filepath.stat().st_size
        file_bytes = filepath.read_bytes()

        if self._dev_mode:
            sha256 = hashlib.sha256(file_bytes).hexdigest()
            file_id = f"DUMMY_FILE_{filename.replace('.', '_').upper()}"
            web_link = f"https://drive.google.com/file/d/{file_id}/view"
            log.info(
                "drive.upload_file.simulated",
                filename=filename,
                filepath=str(filepath),
                size_bytes=size_bytes,
                size_mb=round(size_bytes / 1_048_576, 2),
                strategy="simple" if size_bytes <= SIMPLE_UPLOAD_LIMIT else "resumable",
                sha256=sha256[:16] + "...",
                file_id=file_id,
            )
            return file_id, web_link

        fh = io.BytesIO(file_bytes)
        return self._upload_stream(
            fh,
            filename=filename,
            size_bytes=size_bytes,
            mime_type=mime_type,
            folder_id=folder_id or settings.GOOGLE_DRIVE_FOLDER_ID,
            original_bytes=file_bytes,
        )

    def get_file_url(self, file_id: str) -> str:
        """
        Gibt den Browser-Link zu einer Drive-Datei zurück.

        Args:
            file_id: Google Drive Datei-ID

        Returns:
            webViewLink (https://drive.google.com/file/d/{id}/view)
        """
        if self._dev_mode:
            url = f"https://drive.google.com/file/d/{file_id}/view"
            log.debug("drive.get_file_url.simulated", file_id=file_id, url=url)
            return url

        try:
            result = (
                self._service.files()
                .get(fileId=file_id, fields="webViewLink")
                .execute()
            )
            return result.get("webViewLink", "")
        except Exception as exc:
            log.error("drive.get_file_url.error", file_id=file_id, error=str(exc))
            raise DriveUploadError(f"Kann URL nicht abrufen für {file_id}: {exc}") from exc

    def soft_delete(self, file_id: str, original_name: str) -> None:
        """
        Soft-Delete: Benennt Datei zu "DELETED_<timestamp>_<name>" um.
        Keine echte Löschung (laut Spezifikation: keine files.delete).

        Args:
            file_id:       Drive-Datei-ID
            original_name: Ursprünglicher Dateiname (für neuen Namen)
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        new_name = f"DELETED_{ts}_{original_name}"

        if self._dev_mode:
            log.info(
                "drive.soft_delete.simulated",
                file_id=file_id,
                original_name=original_name,
                new_name=new_name,
            )
            return

        try:
            self._service.files().update(
                fileId=file_id,
                body={"name": new_name},
            ).execute()
            log.info(
                "drive.soft_delete.ok",
                file_id=file_id,
                new_name=new_name,
            )
        except Exception as exc:
            log.error("drive.soft_delete.error", file_id=file_id, error=str(exc))
            raise DriveUploadError(f"Soft-Delete fehlgeschlagen für {file_id}: {exc}") from exc

    # ── Interne Methoden ───────────────────────────────────────────────────────

    def _build_service(self) -> Any:
        """
        Baut den Google Drive API Service via Service Account.

        Credential-Quellen (Priorität):
          1. GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT — roher JSON-String (Railway/Env-Var)
          2. GOOGLE_SERVICE_ACCOUNT_JSON          — Dateipfad (lokal)

        Lazy-Import damit Dev-Mode ohne google-Bibliotheken läuft.
        """
        try:
            import json
            from google.oauth2 import service_account  # type: ignore
            from googleapiclient.discovery import build  # type: ignore

            scopes = ["https://www.googleapis.com/auth/drive"]

            if settings.GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT:
                # Railway / Cloud: JSON direkt als Env-Var
                info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT)
                creds = service_account.Credentials.from_service_account_info(
                    info, scopes=scopes
                )
                log.info("drive_client.authenticated", source="env_var_content")
            else:
                # Lokal: JSON-Datei
                creds = service_account.Credentials.from_service_account_file(
                    settings.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes
                )
                log.info(
                    "drive_client.authenticated",
                    source="file",
                    path=settings.GOOGLE_SERVICE_ACCOUNT_JSON,
                )

            return build("drive", "v3", credentials=creds, cache_discovery=False)

        except FileNotFoundError as exc:
            log.error(
                "drive_client.auth_error",
                error="Service-Account-Datei nicht gefunden",
                path=settings.GOOGLE_SERVICE_ACCOUNT_JSON,
            )
            raise DriveAuthError(f"Service-Account-Datei nicht gefunden: {exc}") from exc
        except Exception as exc:
            log.error("drive_client.auth_error", error=str(exc))
            raise DriveAuthError(f"Drive-Auth fehlgeschlagen: {exc}") from exc

    def _upload_stream(
        self,
        fh: io.BytesIO,
        filename: str,
        size_bytes: int,
        mime_type: str,
        folder_id: str,
        original_bytes: bytes,
    ) -> tuple[str, str]:
        """
        Führt den eigentlichen Upload durch — Simple oder Resumable.

        Simple Upload (≤ 5 MB):    MediaIoBaseUpload, resumable=False
        Resumable Upload (> 5 MB): MediaIoBaseUpload, resumable=True, chunksize=5 MB
        """
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore

        local_sha256 = hashlib.sha256(original_bytes).hexdigest()
        local_md5 = hashlib.md5(original_bytes).hexdigest()
        strategy = "simple" if size_bytes <= SIMPLE_UPLOAD_LIMIT else "resumable"

        metadata: dict = {
            "name": filename,
            "parents": [folder_id] if folder_id else [],
        }

        media = MediaIoBaseUpload(
            fh,
            mimetype=mime_type,
            chunksize=SIMPLE_UPLOAD_LIMIT,
            resumable=(strategy == "resumable"),
        )

        log.info(
            "drive.upload.start",
            filename=filename,
            size_bytes=size_bytes,
            size_mb=round(size_bytes / 1_048_576, 2),
            strategy=strategy,
            folder_id=folder_id,
        )

        try:
            result = (
                self._service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields="id,webViewLink,md5Checksum,size",
                )
                .execute()
            )
        except Exception as exc:
            log.error("drive.upload.error", filename=filename, error=str(exc))
            raise DriveUploadError(f"Upload fehlgeschlagen für '{filename}': {exc}") from exc

        file_id = result["id"]
        web_link = result.get("webViewLink", "")
        drive_md5 = result.get("md5Checksum", "")

        # ── Integritäts-Prüfung (MD5: lokal vs. Drive) ────────────────────────
        if drive_md5 and local_md5 != drive_md5:
            log.error(
                "drive.upload.integrity_mismatch",
                filename=filename,
                file_id=file_id,
                local_md5=local_md5,
                drive_md5=drive_md5,
            )
            raise DriveIntegrityError(
                f"Hash-Mismatch nach Upload '{filename}': lokal={local_md5}, drive={drive_md5}"
            )

        log.info(
            "drive.upload.ok",
            filename=filename,
            file_id=file_id,
            size_bytes=size_bytes,
            strategy=strategy,
            sha256=local_sha256[:16] + "...",
            md5_verified=bool(drive_md5),
        )

        return file_id, web_link


# ── Singleton ──────────────────────────────────────────────────────────────────

_drive_client: DriveClient | None = None


def get_drive_client() -> DriveClient:
    """
    Gibt die gecachte DriveClient-Instanz zurück.
    Lazy-Initialisierung — erst beim ersten Aufruf gebaut.
    """
    global _drive_client
    if _drive_client is None:
        _drive_client = DriveClient()
    return _drive_client
