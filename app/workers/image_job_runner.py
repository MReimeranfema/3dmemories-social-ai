"""
ImageJobRunner — Orchestriert die vollständige Bildgenerierungs-Pipeline.

Spezifikation:
  image-generation/image-job-lifecycle.md
  image-generation/image-generation-architecture.md
  image-generation/image-error-handling.md

Pipeline-Schritte:
  1.  Brief laden (production_blocked=False prüfen)              → BRIEF_LOADED
  2.  Prompt erzeugen (ImagePromptBuilder)                       → PROMPT_BUILT
  3.  Bildgenerierung starten (NanoBananaClient)                 → GENERATION_STARTED
  4.  Bild empfangen                                             → GENERATION_DONE
  5.  QC ausführen (ImageQCService)                              → QC_RUNNING
  6a. QC passed/manual_review → Bild speichern                   → QC_PASSED / QC_MANUAL_REVIEW
  6b. QC auto_reject → Retry mit modifiziertem Prompt            → QC_FAILED → RETRY
  7.  Upload (ImageStorageService)                               → UPLOADING → UPLOAD_DONE
  8.  Slack-Review-Nachricht senden                              → SLACK_SENT
  9.  Google Sheets aktualisieren
  10. ImageJobResult zurückgeben                                 → COMPLETED / FAILED

Retry-Logik:
  - QC auto_reject: max IMAGE_MAX_RETRIES Versuche
  - Negativprompt erhält pro Retry zusätzliche QC-Hinweis-Tokens
  - NanoBananaQuotaError: Celery-Retry (expo., max 3×, 60s base)
  - NanoBananaContentError: sofort FAILED (Prompt-Änderung nötig)
  - NanoBananaAuthError:    sofort FAILED (kein Retry)

Celery-Task: image_generation_job (Queue: high)
Standalone:  ImageJobRunner(brief, request, db).run() → ImageJobResult
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from app.config import settings
from app.models.content_brief import ContentBriefRecord
from app.models.image_generation import (
    ImageGenerationRequest,
    ImageJobResult,
    ImageJobStatus,
    QCStatus,
)
from app.services.image_prompt_builder import ImagePromptBuilder
from app.services.image_qc_service import ImageQCService
from app.services.image_storage_service import ImageStorageService
from app.services.nano_banana_client import (
    NanoBananaAuthError,
    NanoBananaClient,
    NanoBananaCostCapError,
    NanoBananaContentError,
    NanoBananaError,
    NanoBananaQuotaError,
    get_nano_banana_client,
)
from app.services.slack_client import SlackClient, get_slack_client
from app.services.sheets_client import SheetsClient, get_sheets_client

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# QC-Code → Retry-Negative-Tokens
# ─────────────────────────────────────────────────────────────────────────────

# Bei QC-Versagen werden diese Tokens dem Negativprompt hinzugefügt,
# um den nächsten Generierungsversuch zu lenken.

QC_RETRY_NEGATIVE_MAP: dict[str, str] = {
    # Hard Errors
    "QC-HE-001": (
        "ai artifact, visual distortion, digital glitch, rendering error, "
        "pixelated surface, blurry texture, compression artifact, warped object"
    ),
    "QC-HE-003": (
        "off-brand color, wrong color palette, mismatched brand, "
        "cluttered background, busy composition, competing elements"
    ),
    "QC-HE-004": (
        "wrong aspect ratio, cut off edges, poor crop, missing subject"
    ),
    "QC-HE-005": (
        "price label, euro sign, sale badge, promotional text, "
        "advertising overlay, discount sticker"
    ),
    "QC-HE-006": (
        "broken anatomy, impossible body, contorted figure, "
        "melting limbs, fused body parts"
    ),

    # Soft Warnings — Artefakte
    "QC-WN-ARTF-01": "surface noise, film grain, digital artifacts, moire pattern",
    "QC-WN-ARTF-02": "edge aliasing, jagged edges, staircase effect",

    # Anatomie
    "QC-WN-ANAT-01": "awkward pose, unnatural position, stiff posture",
    "QC-WN-ANAT-02": "extra limb, missing limb, floating appendage",

    # Hände
    "QC-WN-HAND-01": (
        "extra fingers, missing fingers, merged fingers, deformed hand, "
        "claw hand, broken wrist"
    ),

    # Gesicht
    "QC-WN-FACE-01": "asymmetric face, distorted expression, uncanny valley, frozen expression",
    "QC-WN-FACE-02": "dead eyes, glassy eyes, unfocused stare",

    # Belichtung
    "QC-WN-LGHT-01": "harsh shadows, flat lighting, overexposed highlights, deep shadows",
    "QC-WN-LGHT-02": "underexposed, muddy colors, low contrast",

    # Komposition
    "QC-WN-COMP-01": "subject cut off, poor framing, wrong rule of thirds",
    "QC-WN-COMP-02": "cluttered scene, busy background, distracting elements",

    # Emotion
    "QC-WN-EMOT-01": "cold expression, emotionless, robotic, expressionless face",

    # Format
    "QC-WN-FMRT-01": "wrong dimensions, stretched image, squished subject",

    # Text
    "QC-WN-TEXT-01": "overlaid text, embedded caption, watermark text, subtitle burn-in",
}


def _build_retry_negative_tokens(qc_result: Any) -> list[str]:
    """
    Erzeugt zusätzliche Negativprompt-Tokens aus einem QC-Ergebnis.
    Wird bei Retry-Versuchen an den ImagePromptBuilder übergeben.
    """
    extra: list[str] = []

    # Hard-Error-Tokens
    for he in getattr(qc_result, "hard_errors", []):
        tokens = QC_RETRY_NEGATIVE_MAP.get(he.code, "")
        if tokens:
            extra.append(tokens)

    # Warning-Tokens (nur wenn Score-Impact stark negativ)
    for w in getattr(qc_result, "warnings", []):
        if getattr(w, "score_impact", 0.0) <= -0.5:
            tokens = QC_RETRY_NEGATIVE_MAP.get(w.code, "")
            if tokens:
                extra.append(tokens)

    return extra


# ─────────────────────────────────────────────────────────────────────────────
# Slack-Nachrichtenformatierung
# ─────────────────────────────────────────────────────────────────────────────

def _build_slack_review_blocks(result: ImageJobResult) -> list[dict]:
    """
    Baut Slack Block-Kit-Blocks für die Review-Nachricht.
    Enthält: Status, Brief-ID, QC-Score, Drive-Link, Kurzinfo.
    """
    asset     = result.asset
    qc_result = result.qc_result
    status    = result.status.value if result.status else "unknown"

    color_map = {
        ImageJobStatus.COMPLETED.value:       "good",
        ImageJobStatus.QC_MANUAL_REVIEW.value: "warning",
        ImageJobStatus.FAILED.value:          "danger",
    }
    color = color_map.get(status, "#cccccc")

    qc_score  = round(qc_result.qc_score_total, 2) if qc_result else "—"
    qc_status = qc_result.qc_status.value if qc_result else "—"
    drive_url = asset.drive_url if asset else ""
    resolution = f"{asset.width}×{asset.height}" if asset else "—"
    size_kb   = round(asset.file_size_bytes / 1024, 1) if asset else "—"

    title_map = {
        ImageJobStatus.COMPLETED.value:       "Bild bereit",
        ImageJobStatus.QC_MANUAL_REVIEW.value: "Bild: Manuelle Prüfung",
        ImageJobStatus.FAILED.value:          "Bild FEHLGESCHLAGEN",
    }
    title = title_map.get(status, f"Bild-Job: {status}")

    text_lines = [
        f"*{title}*",
        f"Brief: `{result.brief_id}` | Platform: `{result.platform}` | Typ: `{result.content_type}`",
        f"QC-Score: *{qc_score}* | QC-Status: `{qc_status}`",
        f"Auflösung: {resolution} | Größe: {size_kb} KB",
    ]
    if drive_url:
        text_lines.append(f"<{drive_url}|In Drive anzeigen>")
    if result.error_message:
        text_lines.append(f":warning: Fehler: `{result.error_message}`")

    hard_errors = qc_result.hard_errors if qc_result else []
    if hard_errors:
        codes = ", ".join(he.code for he in hard_errors)
        text_lines.append(f":x: Hard-Errors: `{codes}`")

    warnings = qc_result.warnings if qc_result else []
    if warnings:
        warn_strs = [f"`{w.code}`" for w in warnings[:3]]
        text_lines.append(f":warning: Warnungen: {', '.join(warn_strs)}")

    duration_s = round(result.duration_ms / 1000, 1) if result.duration_ms else "—"
    text_lines.append(
        f"Gesamtkosten: *{round(result.cost_eur_total, 4)} EUR* | Dauer: {duration_s}s"
    )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "\n".join(text_lines),
            },
        },
        {"type": "divider"},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ImageJobRunner
# ─────────────────────────────────────────────────────────────────────────────

class ImageJobRunner:
    """
    Orchestriert die vollständige Bildgenerierungs-Pipeline für einen Job.

    Erzeugt aus einem validierten ContentBriefRecord ein gespeichertes Asset.
    Verwaltet Statuswechsel, Retry-Logik, Slack-Notification und Sheets-Log.

    Verwendung:
        runner = ImageJobRunner(brief, request, db=session)
        result = runner.run()

    Im Dev-Mode (alle API-Keys leer):
        Gibt ein vollständiges ImageJobResult mit Mock-Asset zurück.
        Kein echter API-Call, keine echten Uploads.
    """

    def __init__(
        self,
        brief:          ContentBriefRecord,
        request:        ImageGenerationRequest,
        db=None,
        prompt_builder: ImagePromptBuilder | None = None,
        banana_client:  NanoBananaClient | None = None,
        qc_service:     ImageQCService | None = None,
        storage_service: ImageStorageService | None = None,
        slack_client:   SlackClient | None = None,
        sheets_client:  SheetsClient | None = None,
    ):
        self._brief   = brief
        self._request = request
        self._db      = db

        # Services — alle mit Dev-Mode-Fallback
        self._prompt_builder  = prompt_builder or ImagePromptBuilder()
        self._banana          = banana_client  or get_nano_banana_client()
        self._qc              = qc_service     or ImageQCService(db=db)
        self._storage         = storage_service or ImageStorageService(db=db)
        self._slack           = slack_client   or get_slack_client()
        self._sheets          = sheets_client  or get_sheets_client()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def run(self) -> ImageJobResult:
        """
        Führt den vollständigen Bildgenerierungs-Job aus.

        Returns:
            ImageJobResult — immer, auch bei Fehler (status=FAILED).
            Wirft nie unkontrollierte Exceptions nach außen.
        """
        result = ImageJobResult(
            job_id       = self._request.job_id,
            brief_uuid   = self._request.brief_uuid,
            brief_id     = self._brief.brief_id,
            platform     = self._request.platform,
            content_type = self._request.content_type,
            status       = ImageJobStatus.PENDING,
            started_at   = datetime.now(timezone.utc),
        )

        self._banana.reset_job_cost()

        log.info(
            "image_job.start",
            job_id        = result.job_id,
            brief_id      = result.brief_id,
            platform      = result.platform,
            content_type  = result.content_type,
            requested_by  = self._request.requested_by,
        )

        try:
            result = self._execute_pipeline(result)
        except Exception as exc:
            log.error(
                "image_job.unhandled_error",
                job_id=result.job_id,
                error=str(exc),
                exc_info=True,
            )
            result.status        = ImageJobStatus.FAILED
            result.error_message = f"Unerwarteter Fehler: {exc}"

        # Finale Buchführung
        result.completed_at = datetime.now(timezone.utc)
        result.duration_ms  = int(
            (result.completed_at - result.started_at).total_seconds() * 1000
        )
        result.cost_eur_total = self._banana.accumulated_cost_eur

        log.info(
            "image_job.done",
            job_id       = result.job_id,
            status       = result.status.value,
            duration_ms  = result.duration_ms,
            cost_eur     = round(result.cost_eur_total, 4),
        )

        # Sheets-Log immer schreiben (auch bei Fehler)
        self._log_job_to_sheets(result)

        # ── Pipeline-Validierung (permanent aktiv) ─────────────────────────────
        # Läuft immer, auch bei FAILED. Wirft nie Exceptions nach außen.
        try:
            from app.services.pipeline_validator import get_pipeline_validator
            get_pipeline_validator().validate(result)
        except Exception as _val_exc:
            log.warning(
                "image_job.validation_error",
                job_id=result.job_id,
                error=str(_val_exc),
            )

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Pipeline-Ablauf
    # ─────────────────────────────────────────────────────────────────────────

    def _execute_pipeline(self, result: ImageJobResult) -> ImageJobResult:
        """
        Innerer Pipeline-Ablauf mit Retry-Schleife.
        Statuswechsel werden hier geführt.
        """

        # ── Step 1: Brief prüfen ───────────────────────────────────────────────
        brief = self._brief
        result.status = ImageJobStatus.BRIEF_LOADED
        log.debug("image_job.brief_loaded", job_id=result.job_id, brief_id=brief.brief_id)

        # Retry-Schleife (max IMAGE_MAX_RETRIES, zählt ab 0)
        extra_negative: list[str] = []
        max_retries  = settings.IMAGE_MAX_RETRIES
        last_qc_result = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                result.status      = ImageJobStatus.RETRY
                result.retry_count = attempt
                log.info(
                    "image_job.retry",
                    job_id  = result.job_id,
                    attempt = attempt,
                    max     = max_retries,
                )

            # ── Step 2: Prompt erzeugen ────────────────────────────────────────
            try:
                prompt = self._prompt_builder.build(
                    brief             = brief,
                    platform          = self._request.platform,
                    content_type      = self._request.content_type,
                    quality_boost     = self._request.quality_boost,
                    extra_negative_tokens = extra_negative or None,
                )
                result.prompt = prompt
                result.status = ImageJobStatus.PROMPT_BUILT
                log.debug(
                    "image_job.prompt_built",
                    job_id       = result.job_id,
                    platform     = prompt.platform,
                    content_type = prompt.content_type,
                    token_pos    = prompt.token_count_positive,
                    token_neg    = prompt.token_count_negative,
                    ci_passed    = prompt.ci_checks_passed,
                )

                if not prompt.ci_checks_passed:
                    log.warning(
                        "image_job.ci_violations",
                        job_id     = result.job_id,
                        violations = prompt.ci_violations,
                    )
                    # CI-Verletzungen blockieren — nicht generieren
                    result.status        = ImageJobStatus.FAILED
                    result.error_message = (
                        f"CI-Verletzungen im Prompt: {'; '.join(prompt.ci_violations)}"
                    )
                    return result

            except Exception as exc:
                log.error(
                    "image_job.prompt_build_failed",
                    job_id=result.job_id, error=str(exc),
                )
                result.status        = ImageJobStatus.FAILED
                result.error_message = f"Prompt-Erzeugung fehlgeschlagen: {exc}"
                return result

            # ── Step 3+4: Bildgenerierung ──────────────────────────────────────
            result.status = ImageJobStatus.GENERATION_STARTED
            try:
                image = self._banana.generate(prompt)
                result.image           = image
                result.cost_eur_generation += image.cost_eur
                result.status           = ImageJobStatus.GENERATION_DONE
                log.debug(
                    "image_job.generation_done",
                    job_id      = result.job_id,
                    size_bytes  = len(image.image_bytes),
                    is_mock     = image.is_mock,
                    cost_eur    = image.cost_eur,
                )

            except NanoBananaAuthError as exc:
                log.error("image_job.auth_error", job_id=result.job_id, error=str(exc))
                result.status        = ImageJobStatus.GENERATION_FAILED
                result.error_message = f"Gemini Auth-Fehler: {exc}"
                return result

            except NanoBananaCostCapError as exc:
                log.error("image_job.cost_cap", job_id=result.job_id, error=str(exc))
                result.status        = ImageJobStatus.FAILED
                result.error_message = f"Kosten-Cap überschritten: {exc}"
                return result

            except NanoBananaContentError as exc:
                log.warning("image_job.content_blocked", job_id=result.job_id, error=str(exc))
                result.status        = ImageJobStatus.GENERATION_FAILED
                result.error_message = f"Prompt durch Safety-Filter blockiert: {exc}"
                # Kein Retry möglich — Prompt muss manuell geändert werden
                self._notify_slack_error(result, "Safety-Filter blockiert Prompt")
                return result

            except NanoBananaQuotaError as exc:
                log.warning("image_job.quota_error", job_id=result.job_id, error=str(exc))
                # Quota: Celery-Task-Retry (wird von dort gehandhabt)
                # Im standalone Betrieb → kurzes Warten + 1 eigener Retry
                if attempt < max_retries:
                    log.info("image_job.quota_wait", seconds=60)
                    time.sleep(60)
                    continue
                result.status        = ImageJobStatus.GENERATION_FAILED
                result.error_message = f"Quota erschöpft nach {attempt + 1} Versuchen: {exc}"
                return result

            except NanoBananaError as exc:
                log.error("image_job.generation_error", job_id=result.job_id, error=str(exc))
                if attempt < max_retries:
                    time.sleep(5 * (attempt + 1))
                    continue
                result.status        = ImageJobStatus.GENERATION_FAILED
                result.error_message = f"Generierungsfehler: {exc}"
                return result

            # ── Step 5: QC ausführen ───────────────────────────────────────────
            result.status = ImageJobStatus.QC_RUNNING
            asset_id_for_qc = f"{result.job_id}-QC{attempt}"  # Temp-ID für QC-Phase

            try:
                qc_result = self._qc.run_qc(
                    image       = image,
                    prompt      = prompt,
                    brief       = brief,
                    job_id      = result.job_id,
                    asset_id    = asset_id_for_qc,
                    retry_count = attempt,
                )
                result.qc_result        = qc_result
                result.cost_eur_qc     += getattr(qc_result, "qc_cost_eur", 0.0)
                last_qc_result          = qc_result

            except Exception as exc:
                log.error(
                    "image_job.qc_error",
                    job_id=result.job_id, error=str(exc), exc_info=True,
                )
                result.status        = ImageJobStatus.QC_FAILED
                result.error_message = f"QC-Fehler: {exc}"
                return result

            # ── Step 6a/6b: QC-Entscheidung ───────────────────────────────────
            if qc_result.qc_status == QCStatus.AUTO_REJECT:
                log.warning(
                    "image_job.qc_auto_reject",
                    job_id      = result.job_id,
                    attempt     = attempt,
                    qc_score    = qc_result.qc_score_total,
                    hard_errors = [he.code for he in qc_result.hard_errors],
                )
                result.status = ImageJobStatus.QC_FAILED

                if attempt >= max_retries:
                    result.status        = ImageJobStatus.FAILED
                    result.error_message = (
                        f"QC nach {attempt + 1} Versuchen nicht bestanden. "
                        f"Score: {qc_result.qc_score_total:.2f}. "
                        f"Hard-Errors: {[he.code for he in qc_result.hard_errors]}"
                    )
                    self._notify_slack_error(result, "QC nach Max-Retries fehlgeschlagen")
                    return result

                # Negativprompt für nächsten Versuch anreichern
                extra_negative = _build_retry_negative_tokens(qc_result)
                continue  # Nächster Retry

            # QC bestanden (passed oder manual_review)
            if qc_result.qc_status == QCStatus.PASSED:
                result.status = ImageJobStatus.QC_PASSED
            else:
                result.status = ImageJobStatus.QC_MANUAL_REVIEW

            log.info(
                "image_job.qc_passed",
                job_id     = result.job_id,
                qc_status  = qc_result.qc_status.value,
                qc_score   = qc_result.qc_score_total,
                warnings   = len(qc_result.warnings),
                attempt    = attempt,
            )
            break  # QC bestanden — Retry-Schleife verlassen

        # ── Step 7: Bild speichern ─────────────────────────────────────────────
        result.status = ImageJobStatus.UPLOADING
        try:
            asset = self._storage.store(
                image         = image,
                prompt        = prompt,
                qc_result     = last_qc_result,
                brief_uuid    = self._request.brief_uuid,
                brief_id      = brief.brief_id,
                job_id        = result.job_id,
                asset_version = result.retry_count + 1,
            )
            result.asset  = asset
            result.status = ImageJobStatus.UPLOAD_DONE
            log.info(
                "image_job.upload_done",
                job_id    = result.job_id,
                asset_id  = asset.asset_id,
                drive_url = asset.drive_url,
            )

        except ValueError as exc:
            # GDPR-Block (gdpr_event_triggered=True)
            log.error("image_job.gdpr_block", job_id=result.job_id, error=str(exc))
            result.status        = ImageJobStatus.UPLOAD_FAILED
            result.error_message = str(exc)
            self._notify_slack_error(result, "GDPR-Verstoß — Bild nicht gespeichert")
            return result

        except Exception as exc:
            log.error("image_job.upload_failed", job_id=result.job_id, error=str(exc))
            result.status        = ImageJobStatus.UPLOAD_FAILED
            result.error_message = f"Drive-Upload fehlgeschlagen: {exc}"
            return result

        # ── Step 8: Slack-Review senden ────────────────────────────────────────
        result.status = ImageJobStatus.COMPLETED  # Vorab setzen für Slack-Nachricht

        try:
            slack_ts, slack_channel = self._send_slack_review(result)
            result.slack_ts      = slack_ts
            result.slack_channel = slack_channel
            result.status        = ImageJobStatus.SLACK_SENT
            log.info(
                "image_job.slack_sent",
                job_id  = result.job_id,
                channel = slack_channel,
                ts      = slack_ts,
            )
        except Exception as exc:
            # Slack-Fehler blockiert nicht — Job trotzdem completed
            log.warning("image_job.slack_failed", job_id=result.job_id, error=str(exc))

        result.status = ImageJobStatus.COMPLETED
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Slack-Benachrichtigung
    # ─────────────────────────────────────────────────────────────────────────

    def _send_slack_review(self, result: ImageJobResult) -> tuple[str, str]:
        """Sendet die Review-Nachricht an #3dm-image-review."""
        channel = settings.SLACK_CHANNEL_IMAGE_REVIEW
        blocks  = _build_slack_review_blocks(result)

        # Kurz-Text als Fallback für Benachrichtigungen ohne Block-Rendering
        status_label = result.status.value
        qc_score = (
            round(result.qc_result.qc_score_total, 2)
            if result.qc_result else "—"
        )
        text = (
            f"Bild-Job {result.job_id} [{status_label}] — "
            f"Brief: {result.brief_id} | QC: {qc_score}"
        )

        ts = self._slack.post_message(
            channel=channel,
            text=text,
            blocks=blocks,
        )
        return ts, channel

    def _notify_slack_error(self, result: ImageJobResult, reason: str) -> None:
        """Sendet Alert bei kritischem Job-Fehler. Graceful."""
        try:
            self._slack.post_alert(
                text=(
                    f":x: Bild-Job fehlgeschlagen: `{result.job_id}`\n"
                    f"Brief: `{result.brief_id}` | Grund: {reason}\n"
                    f"Fehler: {result.error_message}"
                ),
                level=2,
            )
        except Exception as exc:
            log.warning("image_job.slack_error_notify_failed", error=str(exc))

    # ─────────────────────────────────────────────────────────────────────────
    # Google Sheets Log
    # ─────────────────────────────────────────────────────────────────────────

    def _log_job_to_sheets(self, result: ImageJobResult) -> None:
        """Schreibt Job-Zusammenfassung in Sheets-Tab. Graceful."""
        if self._sheets is None:
            return
        try:
            qc_score = (
                round(result.qc_result.qc_score_total, 2)
                if result.qc_result else None
            )
            qc_status = (
                result.qc_result.qc_status.value
                if result.qc_result else None
            )
            hard_errors = (
                ",".join(he.code for he in result.qc_result.hard_errors)
                if result.qc_result else ""
            )
            drive_url = result.asset.drive_url if result.asset else ""

            self._sheets.append_job_row(
                data={
                    "job_id":           result.job_id,
                    "brief_id":         result.brief_id,
                    "platform":         result.platform,
                    "content_type":     result.content_type,
                    "status":           result.status.value,
                    "qc_score":         qc_score,
                    "qc_status":        qc_status,
                    "hard_errors":      hard_errors,
                    "retry_count":      result.retry_count,
                    "cost_eur_total":   round(result.cost_eur_total, 4),
                    "duration_ms":      result.duration_ms,
                    "drive_url":        drive_url,
                    "error_message":    result.error_message,
                    "started_at":       result.started_at.isoformat(),
                    "completed_at":     result.completed_at.isoformat() if result.completed_at else "",
                },
            )
        except Exception as exc:
            log.warning(
                "image_job.sheets_log_failed",
                job_id=result.job_id,
                error=str(exc),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Celery-Task
# ─────────────────────────────────────────────────────────────────────────────

def _run_image_job_celery(
    brief_uuid_str: str,
    platform:       str,
    content_type:   str,
    requested_by:   str,
    quality_boost:  bool = False,
) -> dict:
    """
    Celery-Task-Kernlogik (ohne Celery-Dekorator — für leichteres Testen).

    Lädt Brief aus DB, erstellt ImageGenerationRequest, führt Job aus.
    Gibt das Job-Ergebnis als dict zurück.

    In der Celery-Task-Definition (tasks.py) wird diese Funktion
    mit @celery_app.task dekoriert und an die Celery-Queue gebunden.
    """
    from uuid import UUID
    from app.database import get_db_session
    from app.services.image_generation_service import ImageGenerationService

    brief_uuid = UUID(brief_uuid_str)
    job_id     = f"IMG-{datetime.now(timezone.utc).strftime('%Y')}-{uuid.uuid4().hex[:6].upper()}"

    request = ImageGenerationRequest(
        brief_uuid    = brief_uuid,
        platform      = platform,
        content_type  = content_type,
        job_id        = job_id,
        requested_by  = requested_by,
        quality_boost = quality_boost,
    )

    with get_db_session() as db:
        svc    = ImageGenerationService(db=db)
        result = svc.generate_image(request)

    return {
        "job_id":        result.job_id,
        "status":        result.status.value,
        "brief_id":      result.brief_id,
        "drive_url":     result.drive_url,
        "cost_eur":      round(result.cost_eur_total, 4),
        "duration_ms":   result.duration_ms,
        "error_message": result.error_message,
    }
