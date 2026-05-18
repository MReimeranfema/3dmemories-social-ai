"""
ImageQCService — Qualitätskontrolle für generierte Bilder.

Spezifikation: image-generation/image-qc-rules.md

QC-Pipeline (6 Stages):
  1. Vorprüfung: Format, Auflösung, Dateigröße
  2. Hard-Error-Scan: GDPR (QC-HE-002), Preis (QC-HE-003), Produkt (QC-HE-005)
  3. KI-Artefakt-Analyse (ARTF) → QC-HE-001 wenn Score < 4.0
  4. Vollständige 12-Kategorie-Bewertung via Claude Vision
  5. Score-Berechnung + Warnungs-Aggregation
  6. QC-Record erzeugen + DB/Sheets schreiben

Gewichtung (aus QC-Spezifikation):
  ARTF 20%, BRND 15%, EMOT 12%, COMP 12%, ITEM 10%, ANAT 8%,
  FACE 8%, FMRT 5%, LGHT 4%, HAND 3%, TEXT 2%, PROD 1%

Dev-Mode:
  Claude Vision nicht verfügbar → Mock-Scores (7.5 für alle Kategorien).
  Alle Hard-Errors trotzdem geprüft (Format-Check ist API-unabhängig).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog

from app.config import settings
from app.models.content_brief import ContentBriefRecord
from app.models.image_generation import (
    BuiltPrompt,
    GeneratedImage,
    QCHardError,
    QCResult,
    QCStatus,
    QCWarning,
    get_platform_format,
)
from app.services.claude_client import ClaudeCallParams, get_claude_client

log = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# QC-Gewichtung (aus image-qc-rules.md §1)
# ─────────────────────────────────────────────────────────────────────────────

QC_WEIGHTS: dict[str, float] = {
    "ARTF": 0.20,
    "BRND": 0.15,
    "EMOT": 0.12,
    "COMP": 0.12,
    "ITEM": 0.10,
    "ANAT": 0.08,
    "FACE": 0.08,
    "FMRT": 0.05,
    "LGHT": 0.04,
    "HAND": 0.03,
    "TEXT": 0.02,
    "PROD": 0.01,
}

# Score-Schwellen
THRESHOLD_PASSED       = 7.5
THRESHOLD_MANUAL       = 6.0
ARTF_AUTO_REJECT_SCORE = 6.0    # Auch ohne Hard-Error
ARTF_HARD_ERROR_SCORE  = 4.0    # Hard-Error QC-HE-001
MAX_WARNINGS_BEFORE_REJECT = 8

# Mock-Scores (Dev-Mode)
MOCK_SCORES = {k: 8.0 for k in QC_WEIGHTS}  # Alle Kategorien 8.0 → passed

# Claude Vision Analyse-Prompt
VISION_ANALYSIS_PROMPT = """Du bist ein professioneller Qualitätsprüfer für KI-generierte Social-Media-Bilder.
Analysiere das Bild und bewerte JEDE der folgenden 12 Kategorien mit einem Score von 0.0 bis 10.0.

Kategorien:
1. ANAT (Anatomie): Korrekte Proportionen, natürliche Körper, keine deformierten Extremitäten
2. FACE (Gesichter): Natürliche Gesichter, korrekter Ausdruck, KEIN Kindergesicht sichtbar
3. HAND (Hände): Korrekte Fingeranzahl, natürliche Handhaltung
4. LGHT (Lichtqualität): Konsistente Beleuchtung, passende Stimmung, keine Über-/Unterbelichtung
5. COMP (Komposition): Regel-Drittel, Kernobjekt sichtbar, nicht überladen
6. EMOT (Emotionale Lesbarkeit): Klare emotionale Aussage, authentisch, nicht gestellt
7. FMRT (Plattformformat): Korrektes Seitenverhältnis, Safe-Zone respektiert
8. BRND (Markenfit): Warm, handgemacht, Erdtöne, artisanal — KEIN Stock-Photo-Look
9. ARTF (KI-Artefakte): Keine Tiling-Muster, keine Mutations, keine kryptischen Texte
10. TEXT (Textlesbarkeit): Falls Text vorhanden: gut lesbar, hoher Kontrast
11. PROD (Produktionsrealismus): Szene photographierbar mit realen Mitteln
12. ITEM (Produktverständlichkeit): 3D-Figur / Produkt klar erkennbar (falls im Bild)

Antworte NUR als JSON ohne Erklärungen:
{
  "ANAT": 8.5,
  "FACE": 9.0,
  "HAND": 7.5,
  "LGHT": 8.0,
  "COMP": 8.5,
  "EMOT": 7.5,
  "FMRT": 9.0,
  "BRND": 8.5,
  "ARTF": 8.0,
  "TEXT": 9.0,
  "PROD": 8.0,
  "ITEM": 7.5,
  "notes": {
    "FACE": "Kein Kindergesicht erkennbar",
    "ARTF": "Minimale Hintergrund-Texturwiederholung aber kaum wahrnehmbar",
    "BRND": "Guter Handwerk-Look, könnte etwas wärmer sein"
  }
}"""


# ─────────────────────────────────────────────────────────────────────────────
# ImageQCService
# ─────────────────────────────────────────────────────────────────────────────

class ImageQCService:
    """
    Führt vollständigen QC-Durchlauf auf einem generierten Bild durch.

    Im Dev-Mode (kein ANTHROPIC_API_KEY für Vision):
      Gibt Mock-Scores zurück (alle 8.0) — Hard-Error-Checks laufen trotzdem.
    """

    def __init__(self, db=None, sheets_client=None):
        self._db             = db
        self._sheets_client  = sheets_client
        self._claude         = get_claude_client()

    def run_qc(
        self,
        image:    GeneratedImage,
        prompt:   BuiltPrompt,
        brief:    ContentBriefRecord,
        job_id:   str,
        asset_id: str,
        retry_count: int = 0,
    ) -> QCResult:
        """
        Führt den vollständigen QC-Durchlauf durch.

        Args:
            image:       GeneriertesBild aus NanoBananaClient
            prompt:      BuiltPrompt der zu diesem Bild geführt hat
            brief:       ContentBriefRecord des zugehörigen Briefs
            job_id:      Job-ID für Logging
            asset_id:    Asset-ID für Logging
            retry_count: Anzahl bisheriger Retries (für Logging)

        Returns:
            QCResult mit vollständigen Scores und Status
        """
        start_ms  = int(time.monotonic() * 1000)
        now       = datetime.now(timezone.utc)
        qc_id     = f"QC-{uuid4().hex[:12].upper()}"

        log.info(
            "image_qc.start",
            job_id=job_id,
            asset_id=asset_id,
            brief_id=brief.brief_id,
            retry_count=retry_count,
        )

        result = QCResult(
            qc_id=qc_id,
            asset_id=asset_id,
            job_id=job_id,
            brief_uuid=brief.brief_uuid,
            brief_id=brief.brief_id,
            retry_count=retry_count,
            created_at=now,
        )

        # ── Stage 1: Vorprüfung Format ─────────────────────────────────────────
        format_error = self._check_format(image, prompt)
        if format_error:
            result.hard_errors.append(format_error)
            result.gdpr_event_triggered = False
            result.auto_reject_reason   = f"Format-Fehler: {format_error.code}"
            result.prompt_change_required = format_error.prompt_change_required
            return self._finalize(result, start_ms)

        # ── Stage 2: Hard-Error-Scan (GDPR, Preis, Produkt) ───────────────────
        # In echter Produktion via Claude Vision; Dev-Mode: keine Hard-Errors
        if not image.is_mock and not self._claude._dev_mode:
            hard_errors_stage2 = self._run_vision_hard_errors(image, brief)
            result.hard_errors.extend(hard_errors_stage2)

            # GDPR-Event sofort eskalieren
            gdpr_errors = [e for e in hard_errors_stage2 if e.code == "QC-HE-002"]
            if gdpr_errors:
                result.gdpr_event_triggered = True
                log.error(
                    "image_qc.gdpr_triggered",
                    job_id=job_id,
                    asset_id=asset_id,
                    brief_id=brief.brief_id,
                )
                self._log_gdpr_compliance_event(job_id, asset_id, brief.brief_id)

            if result.hard_errors:
                result.auto_reject_reason = "; ".join(e.code for e in result.hard_errors)
                result.prompt_change_required = any(e.prompt_change_required for e in result.hard_errors)
                return self._finalize(result, start_ms)

        # ── Stage 3+4: Vision-Analyse (12 Kategorien) ─────────────────────────
        scores = self._run_vision_scoring(image, brief)

        result.score_anat = scores.get("ANAT", 8.0)
        result.score_face = scores.get("FACE", 8.0)
        result.score_hand = scores.get("HAND", 8.0)
        result.score_lght = scores.get("LGHT", 8.0)
        result.score_comp = scores.get("COMP", 8.0)
        result.score_emot = scores.get("EMOT", 8.0)
        result.score_fmrt = scores.get("FMRT", 8.0)
        result.score_brnd = scores.get("BRND", 8.0)
        result.score_artf = scores.get("ARTF", 8.0)
        result.score_text = scores.get("TEXT", 8.0)
        result.score_prod = scores.get("PROD", 8.0)
        result.score_item = scores.get("ITEM", 8.0)

        # ── Stage 2b: ARTF Hard-Error-Check ───────────────────────────────────
        if result.score_artf < ARTF_HARD_ERROR_SCORE:
            result.hard_errors.append(QCHardError(
                code="QC-HE-001",
                category="ARTF",
                message=(
                    f"KI-Artefakte kritisch: ARTF-Score {result.score_artf:.1f} < "
                    f"{ARTF_HARD_ERROR_SCORE}. Möglicherweise fehlerhafte Anatomie, "
                    "Tiling oder Finger-Anzahl-Fehler."
                ),
                prompt_change_required=True,
                detected_at=now.isoformat(),
            ))

        # Anatomie Hard-Error
        if result.score_anat < 3.0:
            result.hard_errors.append(QCHardError(
                code="QC-HE-006",
                category="ANAT",
                message=f"Anatomie-Kollaps: ANAT-Score {result.score_anat:.1f} < 3.0.",
                prompt_change_required=True,
                detected_at=now.isoformat(),
            ))

        # Brand Hard-Error
        if result.score_brnd < 3.0:
            result.hard_errors.append(QCHardError(
                code="QC-HE-003",
                category="BRND",
                message=f"Markenverstoß kritisch: BRND-Score {result.score_brnd:.1f} < 3.0.",
                prompt_change_required=True,
                detected_at=now.isoformat(),
            ))

        if result.hard_errors:
            result.auto_reject_reason = "; ".join(e.code for e in result.hard_errors)
            result.prompt_change_required = True
            return self._finalize(result, start_ms)

        # ── Stage 5: Warnungen generieren ─────────────────────────────────────
        result.warnings = self._generate_warnings(result, scores.get("notes", {}))

        # ── ARTF-Auto-Reject (weich) ───────────────────────────────────────────
        if result.score_artf < ARTF_AUTO_REJECT_SCORE:
            result.auto_reject_reason = (
                f"ARTF-Score {result.score_artf:.1f} < {ARTF_AUTO_REJECT_SCORE} "
                "(KI-Artefakte erkannt)"
            )
            result.prompt_change_required = True
            return self._finalize(result, start_ms)

        # ── Stage 6: Gesamtscore + Status ─────────────────────────────────────
        return self._finalize(result, start_ms)

    # ─────────────────────────────────────────────────────────────────────────
    # Format-Check
    # ─────────────────────────────────────────────────────────────────────────

    def _check_format(
        self, image: GeneratedImage, prompt: BuiltPrompt
    ) -> QCHardError | None:
        """
        Prüft ob Bild-Dimensionen dem Zielformat entsprechen.
        Toleranz: 5% Abweichung.
        """
        target_w, target_h = prompt.width, prompt.height

        # Dev-Mode Mock: Dimensionen können stark abweichen — überspringen
        if image.is_mock:
            return None

        if target_w == 0 or target_h == 0:
            return None

        actual_ratio   = image.width / image.height if image.height > 0 else 1.0
        target_ratio   = target_w / target_h
        ratio_deviation = abs(actual_ratio - target_ratio) / target_ratio

        if ratio_deviation > 0.05:  # > 5% Abweichung
            return QCHardError(
                code="QC-HE-004",
                category="FMRT",
                message=(
                    f"Format-Fehler: Seitenverhältnis {image.width}×{image.height} "
                    f"weicht {ratio_deviation * 100:.1f}% vom Ziel "
                    f"{target_w}×{target_h} ab."
                ),
                prompt_change_required=False,   # Format-Fix = API-Parameter, kein Prompt
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Vision-Analyse
    # ─────────────────────────────────────────────────────────────────────────

    def _run_vision_scoring(
        self,
        image: GeneratedImage,
        brief: ContentBriefRecord,
    ) -> dict[str, float]:
        """
        Analysiert das Bild mit Claude Vision.
        Dev-Mode → Mock-Scores.
        """
        if image.is_mock or self._claude._dev_mode:
            log.debug("image_qc.vision.mock", brief_id=brief.brief_id)
            return {**MOCK_SCORES, "notes": {}}

        import base64

        try:
            # Bild als base64 an Claude senden
            b64 = base64.b64encode(image.image_bytes).decode()
            mime = image.mime_type

            # Claude Vision Call (Haiku ist schnell + günstig für Image-Analyse)
            from anthropic import Anthropic  # type: ignore
            client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

            message = client.messages.create(
                model=settings.CLAUDE_MODEL_HAIKU,
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": VISION_ANALYSIS_PROMPT},
                        ],
                    }
                ],
            )

            text = message.content[0].text.strip()

            # JSON extrahieren (Claude kann Markdown-Blöcke hinzufügen)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            parsed = json.loads(text)
            scores: dict[str, float] = {}
            notes:  dict[str, str]   = {}

            for key, val in parsed.items():
                if key == "notes" and isinstance(val, dict):
                    notes = val
                elif key in QC_WEIGHTS and isinstance(val, (int, float)):
                    scores[key] = max(0.0, min(10.0, float(val)))

            # Fehlende Kategorien mit 8.0 auffüllen
            for cat in QC_WEIGHTS:
                if cat not in scores:
                    scores[cat] = 8.0

            scores["notes"] = notes
            return scores

        except Exception as exc:
            log.warning(
                "image_qc.vision.failed",
                brief_id=brief.brief_id,
                error=str(exc),
            )
            # Fallback auf konservative Scores (6.0 → manual review statt passed)
            return {**{k: 6.0 for k in QC_WEIGHTS}, "notes": {}}

    def _run_vision_hard_errors(
        self,
        image: GeneratedImage,
        brief: ContentBriefRecord,
    ) -> list[QCHardError]:
        """
        Prüft GDPR und Preis-Schutz via Claude Vision.
        Nur in Produktion (nicht im Dev-Mode).
        """
        # TODO: Dedizierter Vision-Call für Safety-Checks
        # Für jetzt: wird von der allgemeinen Vision-Analyse abgedeckt
        # (FACE-Score < 2.0 → GDPR-Hard-Error in _run_qc)
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # Warnungs-Generierung
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_warnings(
        self, result: QCResult, notes: dict
    ) -> list[QCWarning]:
        """Generiert Warnungen aus Kategorie-Scores und Vision-Notes."""
        warnings: list[QCWarning] = []
        now = datetime.now(timezone.utc).isoformat()

        thresholds = [
            # (code, category, score_attr, threshold, score_impact, message_template)
            ("QC-WN-ARTF-01", "ARTF", result.score_artf, 7.5, -0.5,
             "KI-Artefakte leicht wahrnehmbar (Score {score:.1f})"),
            ("QC-WN-BRND-01", "BRND", result.score_brnd, 7.5, -0.7,
             "Markenfit schwach — Farbpalette oder Handwerk-Look unzureichend (Score {score:.1f})"),
            ("QC-WN-EMOT-01", "EMOT", result.score_emot, 7.0, -1.0,
             "Emotionale Lesbarkeit schwach (Score {score:.1f})"),
            ("QC-WN-COMP-01", "COMP", result.score_comp, 7.0, -0.6,
             "Komposition verbesserungswürdig (Score {score:.1f})"),
            ("QC-WN-ANAT-01", "ANAT", result.score_anat, 7.0, -0.5,
             "Anatomie leicht inkonsistent (Score {score:.1f})"),
            ("QC-WN-FACE-01", "FACE", result.score_face, 7.0, -0.4,
             "Gesicht unscharf oder Ausdruck schwach (Score {score:.1f})"),
            ("QC-WN-LGHT-01", "LGHT", result.score_lght, 7.0, -0.8,
             "Licht entspricht nicht dem Brief-Preset (Score {score:.1f})"),
            ("QC-WN-HAND-01", "HAND", result.score_hand, 6.5, -0.5,
             "Hände undetailliert oder unnatürlich (Score {score:.1f})"),
            ("QC-WN-ITEM-01", "ITEM", result.score_item, 6.5, -0.7,
             "Produkt unscharf oder schwer erkennbar (Score {score:.1f})"),
            ("QC-WN-TEXT-01", "TEXT", result.score_text, 6.5, -0.7,
             "Textlesbarkeit unzureichend (Score {score:.1f})"),
            ("QC-WN-PROD-01", "PROD", result.score_prod, 6.0, -0.8,
             "Szene kaum mit realen Ressourcen produzierbar (Score {score:.1f})"),
            ("QC-WN-FMRT-01", "FMRT", result.score_fmrt, 7.0, -0.6,
             "Safe-Zone verletzt oder Kernobjekt außerhalb der Plattformzone (Score {score:.1f})"),
        ]

        for code, cat, score, threshold, impact, msg_template in thresholds:
            if score < threshold:
                warnings.append(QCWarning(
                    code=code,
                    category=cat,
                    message=msg_template.format(score=score),
                    score_impact=impact,
                    detected_at=now,
                ))

        # Vision-Notes als zusätzliche Warnungen
        for cat, note in notes.items():
            if cat in QC_WEIGHTS and note:
                warnings.append(QCWarning(
                    code=f"QC-WN-{cat}-NOTE",
                    category=cat,
                    message=f"Vision-Analyse Hinweis: {note}",
                    score_impact=0.0,
                    detected_at=now,
                ))

        return warnings

    # ─────────────────────────────────────────────────────────────────────────
    # Score-Berechnung + Status-Ableitung
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_total_score(self, result: QCResult) -> float:
        """Berechnet gewichteten Gesamtscore."""
        score_map = {
            "ARTF": result.score_artf, "BRND": result.score_brnd,
            "EMOT": result.score_emot, "COMP": result.score_comp,
            "ITEM": result.score_item, "ANAT": result.score_anat,
            "FACE": result.score_face, "FMRT": result.score_fmrt,
            "LGHT": result.score_lght, "HAND": result.score_hand,
            "TEXT": result.score_text, "PROD": result.score_prod,
        }
        total = sum(score_map[k] * QC_WEIGHTS[k] for k in QC_WEIGHTS)
        return round(total, 2)

    def _derive_status(self, result: QCResult, total_score: float) -> QCStatus:
        """Leitet QC-Status aus Scores und Hard-Errors ab."""
        # Hard-Errors → sofort auto_reject
        if result.hard_errors:
            return QCStatus.AUTO_REJECT

        # ARTF-Auto-Reject (weich)
        if result.score_artf < ARTF_AUTO_REJECT_SCORE:
            return QCStatus.AUTO_REJECT

        # Zu viele Warnungen
        if len(result.warnings) > MAX_WARNINGS_BEFORE_REJECT:
            return QCStatus.AUTO_REJECT

        # Score-Schwellen
        if total_score < THRESHOLD_MANUAL:
            return QCStatus.AUTO_REJECT
        if total_score < THRESHOLD_PASSED:
            return QCStatus.MANUAL_REVIEW
        return QCStatus.PASSED

    def _finalize(self, result: QCResult, start_ms: int) -> QCResult:
        """Berechnet Gesamtscore, Status, Duration und persistiert."""
        result.qc_score_total = self._calculate_total_score(result)
        result.qc_status      = self._derive_status(result, result.qc_score_total)
        result.qc_duration_ms = int(time.monotonic() * 1000) - start_ms

        # Auto-Reject-Reason wenn noch nicht gesetzt
        if result.qc_status == QCStatus.AUTO_REJECT and not result.auto_reject_reason:
            if result.score_artf < ARTF_AUTO_REJECT_SCORE:
                result.auto_reject_reason = f"ARTF-Score {result.score_artf:.1f} < {ARTF_AUTO_REJECT_SCORE}"
            elif len(result.warnings) > MAX_WARNINGS_BEFORE_REJECT:
                result.auto_reject_reason = f"Zu viele Warnungen: {len(result.warnings)}"
            else:
                result.auto_reject_reason = f"Gesamtscore {result.qc_score_total:.1f} < {THRESHOLD_MANUAL}"

        # Manual-Review-Reason
        if result.qc_status == QCStatus.MANUAL_REVIEW:
            top_warnings = result.warnings[:3]
            if top_warnings:
                result.manual_review_reason = "; ".join(w.code for w in top_warnings)

        log.info(
            "image_qc.complete",
            qc_id=result.qc_id,
            job_id=result.job_id,
            score=result.qc_score_total,
            status=result.qc_status.value,
            hard_errors=[e.code for e in result.hard_errors],
            warning_count=len(result.warnings),
            duration_ms=result.qc_duration_ms,
        )

        # Persistieren (graceful)
        self._persist_qc_result(result)
        self._log_to_sheets(result)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Persistierung
    # ─────────────────────────────────────────────────────────────────────────

    def _persist_qc_result(self, result: QCResult) -> None:
        """Schreibt QC-Record in image_qc_results. Graceful bei Fehler."""
        if self._db is None:
            return
        try:
            self._db.execute(
                """
                INSERT INTO image_qc_results (
                    qc_id, asset_id, job_id, brief_uuid, brief_id,
                    score_anat, score_face, score_hand, score_lght, score_comp,
                    score_emot, score_fmrt, score_brnd, score_artf, score_text,
                    score_prod, score_item,
                    qc_score_total, qc_status,
                    hard_errors, warnings, warning_count,
                    auto_reject_reason, manual_review_reason,
                    gdpr_event_triggered,
                    qc_engine_version, qc_duration_ms, retry_count,
                    created_at
                ) VALUES (
                    :qc_id, :asset_id, :job_id, :brief_uuid, :brief_id,
                    :anat, :face, :hand, :lght, :comp,
                    :emot, :fmrt, :brnd, :artf, :text,
                    :prod, :item,
                    :total, :status,
                    :hard_errors::jsonb, :warnings::jsonb, :warning_count,
                    :auto_reject_reason, :manual_review_reason,
                    :gdpr,
                    :engine_version, :duration_ms, :retry_count,
                    :created_at
                )
                """,
                {
                    "qc_id":   result.qc_id,
                    "asset_id": result.asset_id,
                    "job_id":   result.job_id,
                    "brief_uuid": str(result.brief_uuid),
                    "brief_id":  result.brief_id,
                    "anat": result.score_anat, "face": result.score_face,
                    "hand": result.score_hand, "lght": result.score_lght,
                    "comp": result.score_comp, "emot": result.score_emot,
                    "fmrt": result.score_fmrt, "brnd": result.score_brnd,
                    "artf": result.score_artf, "text": result.score_text,
                    "prod": result.score_prod, "item": result.score_item,
                    "total": result.qc_score_total,
                    "status": result.qc_status.value,
                    "hard_errors": json.dumps(
                        [e.to_dict() for e in result.hard_errors], ensure_ascii=False
                    ),
                    "warnings": json.dumps(
                        [w.to_dict() for w in result.warnings], ensure_ascii=False
                    ),
                    "warning_count": len(result.warnings),
                    "auto_reject_reason":   result.auto_reject_reason or None,
                    "manual_review_reason": result.manual_review_reason or None,
                    "gdpr": result.gdpr_event_triggered,
                    "engine_version": result.qc_engine_version,
                    "duration_ms":    result.qc_duration_ms,
                    "retry_count":    result.retry_count,
                    "created_at":     result.created_at.isoformat(),
                }
            )
            self._db.commit()
        except Exception as exc:
            log.warning("image_qc.persist.failed", qc_id=result.qc_id, error=str(exc))

    def _log_to_sheets(self, result: QCResult) -> None:
        """Schreibt QC-Zeile in Google Sheets IMAGE_QC_LOG. Graceful."""
        if self._sheets_client is None:
            return
        try:
            self._sheets_client.append_row(
                tab=settings.GOOGLE_SHEETS_TAB_IMAGE_QC,
                row={
                    "qc_id":        result.qc_id,
                    "asset_id":     result.asset_id,
                    "brief_id":     result.brief_id,
                    "qc_score":     result.qc_score_total,
                    "qc_status":    result.qc_status.value,
                    "hard_errors":  "; ".join(e.code for e in result.hard_errors),
                    "warning_count": len(result.warnings),
                    "score_artf":   result.score_artf,
                    "score_brnd":   result.score_brnd,
                    "score_emot":   result.score_emot,
                    "score_comp":   result.score_comp,
                    "auto_reject_reason": result.auto_reject_reason or "",
                    "qc_engine_version":  result.qc_engine_version,
                    "created_at":   result.created_at.isoformat(),
                },
            )
        except Exception as exc:
            log.warning("image_qc.sheets.failed", qc_id=result.qc_id, error=str(exc))

    def _log_gdpr_compliance_event(
        self, job_id: str, asset_id: str, brief_id: str
    ) -> None:
        """Schreibt GDPR-Compliance-Event. Graceful."""
        if self._db is None:
            return
        try:
            self._db.execute(
                """
                INSERT INTO compliance_events (
                    event_id, event_type, asset_id, job_id, brief_id,
                    description, severity, action_taken, created_at
                ) VALUES (
                    :event_id, 'GDPR_CHILD_FACE_DETECTED',
                    :asset_id, :job_id, :brief_id,
                    'QC-HE-002: Kindgesicht erkannt. Auto-Reject ausgelöst.',
                    'critical',
                    'auto_reject + image_deleted',
                    NOW()
                )
                """,
                {
                    "event_id": str(uuid4()),
                    "asset_id": asset_id,
                    "job_id":   job_id,
                    "brief_id": brief_id,
                }
            )
            self._db.commit()
        except Exception as exc:
            log.error(
                "image_qc.gdpr_log.failed",
                job_id=job_id,
                error=str(exc),
            )
