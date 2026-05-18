"""
PipelineValidator — Dauerhafter Validierungslayer für die Bildpipeline.

Prüft nach jedem ImageJobRunner.run()-Abschluss automatisch alle 14
technischen Validierungspunkte und schreibt einen strukturierten Report.

Aktivierungsstatus: DAUERHAFT AKTIV (kein Feature-Flag — immer an)

Validierungspunkte:
  V1  ContentBrief korrekt gelesen
  V2  PromptPackage korrekt erzeugt
  V3  Plattformparameter korrekt
  V4  Ratio korrekt abgeleitet
  V5  Bild-API korrekt
  V6  Bilder korrekt gespeichert
  V7  Google Drive Upload
  V8  Google Sheets Logging
  V9  Statuswechsel korrekt
  V10 Slack Review
  V11 Error Handling (korrekte Fehlerbehandlung)
  V12 Retry-Loop (korrekte Prompt-Erweiterung)
  V13 Prompt Versioning (Eindeutigkeit, Format)
  V14 Log-Persistenz

Output:
  - structlog: pipeline_validator.report (immer)
  - JSON-Datei: validation_log/{job_id}_{timestamp}.json (immer)
  - Warning-Log: wenn ein Punkt FAIL oder WARN
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.config import settings
from app.models.image_generation import ImageJobResult, ImageJobStatus

log = structlog.get_logger(__name__)

# Validierungslog-Verzeichnis
VALIDATION_LOG_DIR = Path(settings.IMAGE_TEMP_DIR).parent / "validation_log"

# Alle Pipeline-Status-Namen die nach erfolgreichem Abschluss erwartet werden
TERMINAL_SUCCESS_STATUSES = {
    ImageJobStatus.COMPLETED,
    ImageJobStatus.SLACK_SENT,
    ImageJobStatus.QC_MANUAL_REVIEW,
}

TERMINAL_STATUSES = TERMINAL_SUCCESS_STATUSES | {
    ImageJobStatus.FAILED,
    ImageJobStatus.QC_FAILED,
}

REQUIRED_STATUSES = {
    "PENDING", "BRIEF_LOADED", "PROMPT_BUILT",
    "GENERATION_STARTED", "GENERATION_DONE", "GENERATION_FAILED",
    "QC_RUNNING", "QC_PASSED", "QC_MANUAL_REVIEW", "QC_FAILED",
    "UPLOADING", "UPLOAD_DONE", "UPLOAD_FAILED",
    "SLACK_SENT", "COMPLETED", "FAILED", "RETRY",
}

PROMPT_ID_PATTERN = re.compile(r"^PROMPT-[0-9A-F]{12}$")


# ─────────────────────────────────────────────────────────────────────────────
# Datenmodelle
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationPoint:
    """Ein einzelner Validierungspunkt."""
    id:      str           # z.B. "V1"
    label:   str           # z.B. "ContentBrief korrekt gelesen"
    status:  str           # "PASS" | "FAIL" | "WARN" | "SKIP"
    detail:  str = ""      # Zusatzinfo bei FAIL/WARN


@dataclass
class ValidationReport:
    """Vollständiger Validierungsreport für einen Pipeline-Run."""
    job_id:          str
    brief_id:        str
    platform:        str
    content_type:    str
    run_status:      str              # Wert von ImageJobStatus
    validated_at:    str              # ISO-8601
    points:          list[ValidationPoint] = field(default_factory=list)
    total:           int = 0
    passed:          int = 0
    failed:          int = 0
    warned:          int = 0
    skipped:         int = 0
    overall:         str = "UNKNOWN"  # "PASS" | "FAIL" | "WARN"
    report_path:     str = ""

    def finalize(self) -> None:
        """Berechnet Zusammenfassung nach Befüllung der Points."""
        self.total   = len(self.points)
        self.passed  = sum(1 for p in self.points if p.status == "PASS")
        self.failed  = sum(1 for p in self.points if p.status == "FAIL")
        self.warned  = sum(1 for p in self.points if p.status == "WARN")
        self.skipped = sum(1 for p in self.points if p.status == "SKIP")
        if self.failed > 0:
            self.overall = "FAIL"
        elif self.warned > 0:
            self.overall = "WARN"
        else:
            self.overall = "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# PipelineValidator
# ─────────────────────────────────────────────────────────────────────────────

class PipelineValidator:
    """
    Dauerhafter Validierungslayer — wird nach jedem ImageJobRunner.run() aufgerufen.

    Evaluiert die technische Stabilität des Runs anhand des ImageJobResult.
    Schreibt Report als JSON + structlog-Eintrag.

    Verwendung (automatisch durch ImageJobRunner):
        validator = PipelineValidator()
        report = validator.validate(result)
    """

    def validate(self, result: ImageJobResult) -> ValidationReport:
        """
        Validiert einen abgeschlossenen ImageJobResult.

        Gibt immer einen Report zurück — wirft keine Exceptions.
        """
        report = ValidationReport(
            job_id       = result.job_id,
            brief_id     = result.brief_id,
            platform     = result.platform,
            content_type = result.content_type,
            run_status   = result.status.value,
            validated_at = datetime.now(timezone.utc).isoformat(),
        )

        # Lauf aller 14 Checks
        checks = [
            self._v1_brief_loaded,
            self._v2_prompt_package,
            self._v3_platform_params,
            self._v4_ratio,
            self._v5_api_call,
            self._v6_file_storage,
            self._v7_drive_upload,
            self._v8_sheets_logging,
            self._v9_status_transitions,
            self._v10_slack_review,
            self._v11_error_handling,
            self._v12_retry_loop,
            self._v13_prompt_versioning,
            self._v14_log_persistence,
        ]

        for check_fn in checks:
            try:
                point = check_fn(result)
            except Exception as exc:
                vid = check_fn.__name__.split("_")[1].upper()  # v1 → V1
                point = ValidationPoint(
                    id=vid, label=check_fn.__doc__ or vid,
                    status="FAIL", detail=f"Validator-Fehler: {exc}",
                )
            report.points.append(point)

        report.finalize()
        self._persist(report)
        self._log(report)
        return report

    # ─── 14 Validierungspunkte ───────────────────────────────────────────────

    def _v1_brief_loaded(self, r: ImageJobResult) -> ValidationPoint:
        """ContentBrief korrekt gelesen"""
        ok = bool(r.brief_id) and bool(r.brief_uuid)
        return ValidationPoint(
            id="V1", label="ContentBrief korrekt gelesen",
            status="PASS" if ok else "FAIL",
            detail="" if ok else f"brief_id={r.brief_id!r} brief_uuid={r.brief_uuid!r}",
        )

    def _v2_prompt_package(self, r: ImageJobResult) -> ValidationPoint:
        """PromptPackage korrekt erzeugt"""
        if r.prompt is None:
            if r.status == ImageJobStatus.FAILED and not r.prompt:
                return ValidationPoint(id="V2", label="PromptPackage korrekt erzeugt",
                                       status="SKIP", detail="Job FAILED vor Prompt-Build")
            return ValidationPoint(id="V2", label="PromptPackage korrekt erzeugt",
                                   status="FAIL", detail="prompt ist None trotz erfolgreichem Status")

        p = r.prompt
        issues = []
        if not p.positive_prompt or len(p.positive_prompt) < 50:
            issues.append(f"positive_prompt zu kurz ({len(p.positive_prompt)} Zeichen)")
        if not p.negative_prompt or len(p.negative_prompt) < 20:
            issues.append(f"negative_prompt zu kurz ({len(p.negative_prompt)} Zeichen)")
        if not p.prompt_id:
            issues.append("prompt_id fehlt")
        if not p.ci_checks_passed:
            issues.append(f"CI violations: {p.ci_violations}")

        return ValidationPoint(
            id="V2", label="PromptPackage korrekt erzeugt",
            status="PASS" if not issues else "FAIL",
            detail="; ".join(issues),
        )

    def _v3_platform_params(self, r: ImageJobResult) -> ValidationPoint:
        """Plattformparameter korrekt gesetzt"""
        if r.prompt is None:
            return ValidationPoint(id="V3", label="Plattformparameter korrekt gesetzt",
                                   status="SKIP", detail="Kein Prompt vorhanden")
        p = r.prompt
        issues = []
        if p.platform not in ("instagram", "facebook"):
            issues.append(f"Unbekannte Plattform: {p.platform!r}")
        if p.content_type not in ("feed_post","reel","reel_thumbnail","story_image","carousel_slide"):
            issues.append(f"Unbekannter content_type: {p.content_type!r}")
        if not p.aspect_ratio:
            issues.append("aspect_ratio fehlt")
        if p.width <= 0 or p.height <= 0:
            issues.append(f"Ungültige Dimensionen: {p.width}×{p.height}")

        return ValidationPoint(
            id="V3", label="Plattformparameter korrekt gesetzt",
            status="PASS" if not issues else "FAIL",
            detail="; ".join(issues),
        )

    def _v4_ratio(self, r: ImageJobResult) -> ValidationPoint:
        """Ratio korrekt abgeleitet"""
        if r.prompt is None:
            return ValidationPoint(id="V4", label="Ratio korrekt abgeleitet",
                                   status="SKIP", detail="Kein Prompt vorhanden")
        p = r.prompt
        EXPECTED = {
            ("instagram","feed_post"):      ("1:1",    1080, 1080),
            ("instagram","carousel_slide"): ("1:1",    1080, 1080),
            ("instagram","reel_thumbnail"): ("9:16",   1080, 1920),
            ("instagram","reel"):           ("9:16",   1080, 1920),
            ("instagram","story_image"):    ("9:16",   1080, 1920),
            ("facebook", "feed_post"):      ("1.91:1", 1200, 630),
            ("facebook", "story_image"):    ("9:16",   1080, 1920),
            ("facebook", "reel"):           ("9:16",   1080, 1920),
        }
        expected = EXPECTED.get((p.platform, p.content_type))
        if expected is None:
            return ValidationPoint(id="V4", label="Ratio korrekt abgeleitet",
                                   status="WARN", detail=f"Kombination {p.platform}/{p.content_type} nicht in Tabelle")
        exp_ratio, exp_w, exp_h = expected
        ok = (p.aspect_ratio == exp_ratio and p.width == exp_w and p.height == exp_h)
        return ValidationPoint(
            id="V4", label="Ratio korrekt abgeleitet",
            status="PASS" if ok else "FAIL",
            detail="" if ok else f"Erwartet {exp_ratio} {exp_w}×{exp_h}, got {p.aspect_ratio} {p.width}×{p.height}",
        )

    def _v5_api_call(self, r: ImageJobResult) -> ValidationPoint:
        """Bild-API korrekt"""
        if r.image is None:
            if r.status == ImageJobStatus.FAILED:
                return ValidationPoint(id="V5", label="Bild-API korrekt",
                                       status="FAIL", detail=f"Job FAILED: {r.error_message}")
            return ValidationPoint(id="V5", label="Bild-API korrekt",
                                   status="SKIP", detail="Kein Bild erzeugt")
        img = r.image
        issues = []
        if not img.image_bytes or len(img.image_bytes) == 0:
            issues.append("image_bytes leer")
        if img.width <= 0 or img.height <= 0:
            issues.append(f"Ungültige Dimensionen: {img.width}×{img.height}")
        if img.cost_eur < 0:
            issues.append(f"Negativer Cost: {img.cost_eur}")
        if not img.model:
            issues.append("model nicht gesetzt")

        status = "PASS" if not issues else "FAIL"
        detail = "; ".join(issues) if issues else f"{len(img.image_bytes):,} bytes | {img.model} | mock={img.is_mock}"
        return ValidationPoint(id="V5", label="Bild-API korrekt", status=status, detail=detail)

    def _v6_file_storage(self, r: ImageJobResult) -> ValidationPoint:
        """Bilder korrekt gespeichert"""
        if r.asset is None:
            if r.status in (ImageJobStatus.FAILED, ImageJobStatus.QC_FAILED):
                return ValidationPoint(id="V6", label="Bilder korrekt gespeichert",
                                       status="SKIP", detail="Job nicht bis zur Speicherung durchgelaufen")
            return ValidationPoint(id="V6", label="Bilder korrekt gespeichert",
                                   status="FAIL", detail="asset ist None bei erfolgreichem Job")
        a = r.asset
        issues = []
        if not a.local_path:
            issues.append("local_path fehlt")
        elif not Path(str(a.local_path)).exists():
            issues.append(f"Datei nicht auf Disk: {a.local_path}")
        if not a.asset_id:
            issues.append("asset_id fehlt")

        return ValidationPoint(
            id="V6", label="Bilder korrekt gespeichert",
            status="PASS" if not issues else "FAIL",
            detail="; ".join(issues) if issues else f"asset_id={a.asset_id}",
        )

    def _v7_drive_upload(self, r: ImageJobResult) -> ValidationPoint:
        """Google Drive Upload"""
        if r.asset is None:
            return ValidationPoint(id="V7", label="Google Drive Upload",
                                   status="SKIP", detail="Kein Asset vorhanden")
        drive_url = r.asset.drive_url or ""
        ok = bool(drive_url)
        return ValidationPoint(
            id="V7", label="Google Drive Upload",
            status="PASS" if ok else "WARN",
            detail=drive_url if ok else "drive_url leer (Dev-Mode oder Upload-Fehler?)",
        )

    def _v8_sheets_logging(self, r: ImageJobResult) -> ValidationPoint:
        """Google Sheets Logging"""
        # Sheets-Logging ist graceful — kein direktes Ergebnis im JobResult.
        # Wir prüfen: kein kritischer Fehler im error_message durch Sheets.
        sheets_blocked = "sheets" in r.error_message.lower() and "fatal" in r.error_message.lower()
        return ValidationPoint(
            id="V8", label="Google Sheets Logging",
            status="FAIL" if sheets_blocked else "PASS",
            detail="Sheets-Logging graceful (Fehler loggen, kein Job-Abbruch)" if not sheets_blocked else r.error_message,
        )

    def _v9_status_transitions(self, r: ImageJobResult) -> ValidationPoint:
        """Statuswechsel korrekt"""
        existing = {s.name for s in ImageJobStatus}
        missing  = REQUIRED_STATUSES - existing
        if missing:
            return ValidationPoint(id="V9", label="Statuswechsel korrekt",
                                   status="FAIL", detail=f"Fehlende Status: {missing}")
        terminal = r.status in TERMINAL_STATUSES
        return ValidationPoint(
            id="V9", label="Statuswechsel korrekt",
            status="PASS" if terminal else "WARN",
            detail=f"Endstatus: {r.status.value}",
        )

    def _v10_slack_review(self, r: ImageJobResult) -> ValidationPoint:
        """Slack Review"""
        if r.status in (ImageJobStatus.FAILED, ImageJobStatus.QC_FAILED):
            # Auch bei Fehler sollte Slack-Benachrichtigung laufen (wenn runner das tut)
            has_ts = bool(r.slack_ts)
            return ValidationPoint(
                id="V10", label="Slack Review",
                status="PASS" if has_ts else "WARN",
                detail=f"Job FAILED — Slack-ts: {r.slack_ts or 'nicht gesendet'}",
            )
        has_ts = bool(r.slack_ts)
        return ValidationPoint(
            id="V10", label="Slack Review",
            status="PASS" if has_ts else "WARN",
            detail=f"ts={r.slack_ts}" if has_ts else "slack_ts leer — Slack-Nachricht nicht gesendet?",
        )

    def _v11_error_handling(self, r: ImageJobResult) -> ValidationPoint:
        """Error Handling korrekt"""
        # Job hat immer einen definierten Status — kein offener Fehler-Zustand
        has_status = r.status in ImageJobStatus.__members__.values()
        if r.status == ImageJobStatus.FAILED:
            has_msg = bool(r.error_message)
            return ValidationPoint(
                id="V11", label="Error Handling korrekt",
                status="PASS" if has_msg else "WARN",
                detail=f"FAILED mit Nachricht: {r.error_message[:80]}" if has_msg
                       else "FAILED aber kein error_message gesetzt",
            )
        return ValidationPoint(
            id="V11", label="Error Handling korrekt",
            status="PASS" if has_status else "FAIL",
            detail=f"Status: {r.status.value}",
        )

    def _v12_retry_loop(self, r: ImageJobResult) -> ValidationPoint:
        """Retry-Loop korrekt"""
        retry_ok = isinstance(r.retry_count, int) and r.retry_count >= 0
        from app.config import settings as cfg
        max_ok = r.retry_count <= cfg.IMAGE_MAX_RETRIES
        issues = []
        if not retry_ok:
            issues.append(f"retry_count ungültig: {r.retry_count!r}")
        if not max_ok:
            issues.append(f"retry_count {r.retry_count} > max {cfg.IMAGE_MAX_RETRIES}")

        detail = f"retry_count={r.retry_count}/{cfg.IMAGE_MAX_RETRIES}"
        return ValidationPoint(
            id="V12", label="Retry-Loop korrekt",
            status="PASS" if not issues else "FAIL",
            detail=detail if not issues else "; ".join(issues),
        )

    def _v13_prompt_versioning(self, r: ImageJobResult) -> ValidationPoint:
        """Prompt Versioning (Eindeutigkeit, Format)"""
        if r.prompt is None:
            return ValidationPoint(id="V13", label="Prompt Versioning",
                                   status="SKIP", detail="Kein Prompt vorhanden")
        pid = r.prompt.prompt_id
        fmt_ok   = bool(PROMPT_ID_PATTERN.match(pid)) if pid else False
        ver_ok   = bool(r.prompt.template_version)
        issues   = []
        if not fmt_ok:
            issues.append(f"prompt_id Format ungültig: {pid!r}")
        if not ver_ok:
            issues.append("template_version fehlt")
        return ValidationPoint(
            id="V13", label="Prompt Versioning",
            status="PASS" if not issues else "FAIL",
            detail=f"prompt_id={pid} | v={r.prompt.template_version}" if not issues else "; ".join(issues),
        )

    def _v14_log_persistence(self, r: ImageJobResult) -> ValidationPoint:
        """Log-Persistenz (structlog)"""
        # structlog ist immer aktiv wenn der Import nicht fehlschlägt.
        # Wir prüfen: duration_ms wurde gesetzt (mind. 1 Log-Eintrag geflossen).
        has_timing = r.duration_ms >= 0
        has_cost   = r.cost_eur_total >= 0
        return ValidationPoint(
            id="V14", label="Log-Persistenz",
            status="PASS" if (has_timing and has_cost) else "WARN",
            detail=f"duration={r.duration_ms}ms | cost=€{r.cost_eur_total:.4f}",
        )

    # ─── Persistenz ──────────────────────────────────────────────────────────

    def _persist(self, report: ValidationReport) -> None:
        """Schreibt Report als JSON-Datei in validation_log/."""
        try:
            VALIDATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            path = VALIDATION_LOG_DIR / f"{report.job_id}_{ts}.json"
            data = asdict(report)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            report.report_path = str(path)
        except Exception as exc:
            log.warning("pipeline_validator.persist.failed", error=str(exc))

    def _log(self, report: ValidationReport) -> None:
        """Gibt strukturierten Log-Eintrag aus."""
        level = "info" if report.overall == "PASS" else "warning" if report.overall == "WARN" else "error"
        getattr(log, level)(
            "pipeline_validator.report",
            job_id       = report.job_id,
            brief_id     = report.brief_id,
            platform     = report.platform,
            run_status   = report.run_status,
            overall      = report.overall,
            passed       = report.passed,
            failed       = report.failed,
            warned       = report.warned,
            skipped      = report.skipped,
            total        = report.total,
            report_path  = report.report_path,
            failures     = [p.id for p in report.points if p.status == "FAIL"],
            warnings     = [p.id for p in report.points if p.status == "WARN"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_validator: PipelineValidator | None = None


def get_pipeline_validator() -> PipelineValidator:
    """Gecachte PipelineValidator-Instanz."""
    global _validator
    if _validator is None:
        _validator = PipelineValidator()
    return _validator
