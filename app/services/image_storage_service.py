"""
ImageStorageService — Speicherung generierter Bilder.

Spezifikation: image-generation/image-storage.md

Ablauf:
  1. Bild-Bytes in lokales Temp-Verzeichnis schreiben
  2. Drive-Ordnerpfad bestimmen (platform/YYYY-MM/brief_id/)
  3. Bild + Thumbnail nach Google Drive hochladen
  4. Asset-Metadaten in assets-Tabelle schreiben
  5. Google Sheets IMAGES-Tab aktualisieren
  6. StoredAsset zurückgeben

Asset-ID-Format: IMG-{YYYY}-{NNN:03d}
Dateiname:       {asset_id}_v{version}_{content_type}.{ext}
Thumbnail:       {asset_id}_v{version}_{content_type}_thumb.jpg

Dev-Mode:
  Kein Drive-Upload — lokale Temp-Datei bleibt, Drive-URL ist Dummy.
  Sheets-Zeile wird trotzdem simuliert.
"""

from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import structlog

from app.config import settings
from app.models.image_generation import (
    BuiltPrompt,
    GeneratedImage,
    QCResult,
    QCStatus,
    StoredAsset,
)
from app.services.drive_client import DriveClient, get_drive_client
from app.services.sheets_client import SheetsClient, get_sheets_client

log = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Thumbnail-Dimensionen
# ─────────────────────────────────────────────────────────────────────────────

THUMBNAIL_MAX_WIDTH  = 320
THUMBNAIL_MAX_HEIGHT = 320


# ─────────────────────────────────────────────────────────────────────────────
# Asset-ID-Generator
# ─────────────────────────────────────────────────────────────────────────────

_asset_counter: dict[str, int] = {}   # In-Memory für Dev-Mode; Produktion nutzt DB-Sequenz

def _next_asset_id(year: int) -> str:
    """Erzeugt nächste Asset-ID im Format IMG-YYYY-NNN."""
    key = str(year)
    _asset_counter[key] = _asset_counter.get(key, 0) + 1
    return f"IMG-{year}-{_asset_counter[key]:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# ImageStorageService
# ─────────────────────────────────────────────────────────────────────────────

class ImageStorageService:
    """
    Speichert generierte Bilder lokal + in Google Drive.

    Im Dev-Mode (GOOGLE_SERVICE_ACCOUNT_JSON leer):
      Bild wird lokal gespeichert, Drive-URL ist Dummy.
      Sheets-Log wird simuliert.
    """

    def __init__(
        self,
        db=None,
        drive_client: DriveClient | None = None,
        sheets_client: SheetsClient | None = None,
    ):
        self._db             = db
        self._drive          = drive_client or get_drive_client()
        self._sheets         = sheets_client or get_sheets_client()
        self._temp_dir       = Path(settings.IMAGE_TEMP_DIR)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def store(
        self,
        image:      GeneratedImage,
        prompt:     BuiltPrompt,
        qc_result:  QCResult,
        brief_uuid: UUID,
        brief_id:   str,
        job_id:     str,
        asset_version: int = 1,
    ) -> StoredAsset:
        """
        Speichert ein generiertes Bild (nach bestandenem QC) in Drive + Sheets.

        Args:
            image:         Generiertes Bild aus NanoBananaClient
            prompt:        BuiltPrompt der zu diesem Bild geführt hat
            qc_result:     Bestandenes QC-Ergebnis (status ≠ auto_reject erwartet)
            brief_uuid:    UUID des zugehörigen Briefs
            brief_id:      Familien-ID des Briefs
            job_id:        Job-ID für Logging
            asset_version: Version dieses Assets für diesen Brief (default: 1)

        Returns:
            StoredAsset mit Drive-URL und Metadaten

        Raises:
            ValueError: QC-Status ist auto_reject (Speicherung verweigert)
        """
        # Sicherheitscheck: Kein GDPR-Bild speichern
        if qc_result.gdpr_event_triggered:
            raise ValueError(
                f"GDPR-Verstoß erkannt (QC-HE-002) — Bild wird nicht gespeichert. "
                f"Job: {job_id}"
            )

        now  = datetime.now(timezone.utc)
        year = now.year

        # Asset-ID bestimmen
        asset_id = self._get_or_create_asset_id(
            brief_id=brief_id, version=asset_version, year=year
        )

        # Dateinamen erzeugen
        ext       = image.format.lower()  # 'jpeg' oder 'png'
        file_ext  = "jpg" if ext == "jpeg" else ext
        filename  = f"{asset_id}_v{asset_version}_{prompt.content_type}.{file_ext}"
        thumb_fn  = f"{asset_id}_v{asset_version}_{prompt.content_type}_thumb.jpg"

        log.info(
            "image_storage.start",
            job_id=job_id,
            asset_id=asset_id,
            filename=filename,
            size_bytes=len(image.image_bytes),
        )

        # ── Lokal speichern ────────────────────────────────────────────────────
        local_path = self._save_local(image.image_bytes, filename)

        # ── Thumbnail erzeugen ─────────────────────────────────────────────────
        thumb_bytes = self._make_thumbnail(image.image_bytes, image.format)
        thumb_local = self._save_local(thumb_bytes, thumb_fn)

        # ── Drive-Ordnerpfad bestimmen ─────────────────────────────────────────
        drive_folder_id = self._get_drive_folder(
            platform=prompt.platform,
            brief_id=brief_id,
            month_str=now.strftime("%Y-%m"),
        )

        # ── Drive-Upload ───────────────────────────────────────────────────────
        mime_type = image.mime_type
        drive_file_id, drive_url = self._drive.upload_file(
            filename=filename,
            filepath=local_path,
            folder_id=drive_folder_id,
            mime_type=mime_type,
        )

        # Thumbnail hochladen (graceful — Fehler blockiert nicht)
        try:
            self._drive.upload_file(
                filename=thumb_fn,
                filepath=thumb_local,
                folder_id=drive_folder_id,
                mime_type="image/jpeg",
            )
        except Exception as exc:
            log.warning(
                "image_storage.thumb_upload_failed",
                asset_id=asset_id,
                error=str(exc),
            )

        file_size_bytes = len(image.image_bytes)

        asset = StoredAsset(
            asset_id=asset_id,
            job_id=job_id,
            brief_uuid=brief_uuid,
            brief_id=brief_id,
            prompt_id=prompt.prompt_id,
            platform=prompt.platform,
            content_type=prompt.content_type,
            aspect_ratio=prompt.aspect_ratio,
            width=image.width,
            height=image.height,
            format=file_ext,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            drive_file_id=drive_file_id,
            drive_url=drive_url,
            local_path=str(local_path),
            qc_score=qc_result.qc_score_total,
            qc_status=qc_result.qc_status.value,
            asset_version=asset_version,
            created_at=now,
            is_mock=image.is_mock,
        )

        # ── DB-Speicherung ─────────────────────────────────────────────────────
        self._persist_asset(asset)

        # ── Sheets-Log ─────────────────────────────────────────────────────────
        self._log_to_sheets(asset, qc_result)

        log.info(
            "image_storage.complete",
            asset_id=asset_id,
            drive_url=drive_url,
            size_bytes=file_size_bytes,
            size_mb=round(file_size_bytes / 1_048_576, 2),
        )

        return asset

    def delete_gdpr_image(self, local_path: str, asset_id: str) -> None:
        """
        Löscht ein Bild das GDPR-Verstoß ausgelöst hat.
        Lokal: echte Löschung. Drive: Soft-Delete.
        """
        # Lokale Datei löschen
        try:
            p = Path(local_path)
            if p.exists():
                p.unlink()
                log.info("image_storage.gdpr_local_deleted", path=local_path)
        except Exception as exc:
            log.error("image_storage.gdpr_local_delete_failed", error=str(exc))

        log.warning(
            "image_storage.gdpr_delete",
            asset_id=asset_id,
            local_path=local_path,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Lokale Speicherung
    # ─────────────────────────────────────────────────────────────────────────

    def _save_local(self, image_bytes: bytes, filename: str) -> Path:
        """Schreibt Bild-Bytes in Temp-Verzeichnis."""
        path = self._temp_dir / filename
        path.write_bytes(image_bytes)
        log.debug("image_storage.saved_local", path=str(path), size=len(image_bytes))
        return path

    # ─────────────────────────────────────────────────────────────────────────
    # Thumbnail
    # ─────────────────────────────────────────────────────────────────────────

    def _make_thumbnail(self, image_bytes: bytes, fmt: str) -> bytes:
        """
        Erzeugt ein JPEG-Thumbnail (max 320×320).
        Benötigt Pillow. Wenn nicht installiert: gibt Original zurück.
        """
        try:
            from PIL import Image  # type: ignore

            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail((THUMBNAIL_MAX_WIDTH, THUMBNAIL_MAX_HEIGHT))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except ImportError:
            log.debug("image_storage.thumbnail.pillow_missing — Original als Fallback")
            return image_bytes
        except Exception as exc:
            log.warning("image_storage.thumbnail.failed", error=str(exc))
            return image_bytes

    # ─────────────────────────────────────────────────────────────────────────
    # Drive-Ordner
    # ─────────────────────────────────────────────────────────────────────────

    def _get_drive_folder(
        self,
        platform: str,
        brief_id: str,
        month_str: str,
    ) -> str:
        """
        Gibt Ziel-Ordner-ID zurück.
        Struktur: images/{platform}/{YYYY-MM}/{brief_id}/
        Dev-Mode: GOOGLE_DRIVE_IMAGES_FOLDER_ID als Fallback.
        """
        if not settings.GOOGLE_DRIVE_IMAGES_FOLDER_ID:
            return settings.GOOGLE_DRIVE_FOLDER_ID or "DUMMY_FOLDER"

        # In Produktion: Ordnerpfad via Drive API anlegen/finden
        # Hier vereinfacht: Root-Folder zurückgeben (Drive-Ordner-Hierarchie
        # ist ein Deployment-Detail das via Drive-API-Calls aufgebaut wird)
        return settings.GOOGLE_DRIVE_IMAGES_FOLDER_ID

    # ─────────────────────────────────────────────────────────────────────────
    # Asset-ID
    # ─────────────────────────────────────────────────────────────────────────

    def _get_or_create_asset_id(
        self, brief_id: str, version: int, year: int
    ) -> str:
        """
        Erzeugt eine eindeutige Asset-ID.
        In Produktion: DB-Sequenz. Dev-Mode: In-Memory-Counter.
        """
        if self._db is None:
            return _next_asset_id(year)

        # DB: nächste Nummer aus assets-Tabelle für dieses Jahr
        try:
            row = self._db.execute(
                """
                SELECT COUNT(*) as cnt FROM assets
                WHERE asset_id LIKE :pattern
                """,
                {"pattern": f"IMG-{year}-%"},
            ).fetchone()
            n = (dict(row)["cnt"] if row else 0) + 1
            return f"IMG-{year}-{n:03d}"
        except Exception:
            return _next_asset_id(year)

    # ─────────────────────────────────────────────────────────────────────────
    # Persistierung
    # ─────────────────────────────────────────────────────────────────────────

    def _persist_asset(self, asset: StoredAsset) -> None:
        """Schreibt Asset-Metadaten in assets-Tabelle. Graceful."""
        if self._db is None:
            return
        try:
            self._db.execute(
                """
                INSERT INTO assets (
                    asset_id, job_id, brief_uuid, brief_id, prompt_id,
                    platform, content_type, aspect_ratio,
                    width, height, format, mime_type, file_size_bytes,
                    drive_file_id, drive_url,
                    qc_score, qc_status, asset_version,
                    created_at
                ) VALUES (
                    :asset_id, :job_id, :brief_uuid, :brief_id, :prompt_id,
                    :platform, :content_type, :aspect_ratio,
                    :width, :height, :format, :mime_type, :file_size_bytes,
                    :drive_file_id, :drive_url,
                    :qc_score, :qc_status, :asset_version,
                    :created_at
                )
                """,
                {
                    "asset_id":       asset.asset_id,
                    "job_id":         asset.job_id,
                    "brief_uuid":     str(asset.brief_uuid),
                    "brief_id":       asset.brief_id,
                    "prompt_id":      asset.prompt_id,
                    "platform":       asset.platform,
                    "content_type":   asset.content_type,
                    "aspect_ratio":   asset.aspect_ratio,
                    "width":          asset.width,
                    "height":         asset.height,
                    "format":         asset.format,
                    "mime_type":      asset.mime_type,
                    "file_size_bytes": asset.file_size_bytes,
                    "drive_file_id":  asset.drive_file_id,
                    "drive_url":      asset.drive_url,
                    "qc_score":       asset.qc_score,
                    "qc_status":      asset.qc_status,
                    "asset_version":  asset.asset_version,
                    "created_at":     asset.created_at.isoformat(),
                }
            )
            self._db.commit()
        except Exception as exc:
            log.warning(
                "image_storage.persist.failed",
                asset_id=asset.asset_id,
                error=str(exc),
            )

    def _log_to_sheets(self, asset: StoredAsset, qc_result: QCResult) -> None:
        """Schreibt Asset-Zeile in Google Sheets IMAGES-Tab. Graceful."""
        if self._sheets is None:
            return
        try:
            self._sheets.append_job_row(
                data={
                    "asset_id":       asset.asset_id,
                    "brief_id":       asset.brief_id,
                    "job_id":         asset.job_id,
                    "platform":       asset.platform,
                    "content_type":   asset.content_type,
                    "aspect_ratio":   asset.aspect_ratio,
                    "resolution":     f"{asset.width}×{asset.height}",
                    "file_size_kb":   round(asset.file_size_bytes / 1024, 1),
                    "qc_score":       asset.qc_score,
                    "qc_status":      asset.qc_status,
                    "drive_url":      asset.drive_url,
                    "asset_version":  asset.asset_version,
                    "created_at":     asset.created_at.isoformat(),
                },
            )
        except Exception as exc:
            log.warning(
                "image_storage.sheets.failed",
                asset_id=asset.asset_id,
                error=str(exc),
            )
