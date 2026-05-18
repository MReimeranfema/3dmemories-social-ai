"""
Shared Datenmodelle für die Image-Generation-Pipeline.

Alle Typen die zwischen den Services ausgetauscht werden sind hier definiert.
Services importieren aus diesem Modul — keine zirkulären Abhängigkeiten.

Abhängigkeiten:
  image-generation/image-generation-architecture.md
  image-generation/image-job-lifecycle.md
  image-generation/image-qc-rules.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class ImageJobStatus(str, Enum):
    """Zustandsmaschine für einen Image-Job. Aus image-job-lifecycle.md."""
    PENDING             = "pending"
    BRIEF_LOADED        = "brief_loaded"
    PROMPT_BUILT        = "prompt_built"
    GENERATION_STARTED  = "generation_started"
    GENERATION_DONE     = "generation_done"
    GENERATION_FAILED   = "generation_failed"
    QC_RUNNING          = "qc_running"
    QC_PASSED           = "qc_passed"
    QC_MANUAL_REVIEW    = "qc_manual_review"
    QC_FAILED           = "qc_failed"
    UPLOADING           = "uploading"
    UPLOAD_DONE         = "upload_done"
    UPLOAD_FAILED       = "upload_failed"
    SLACK_SENT          = "slack_sent"
    COMPLETED           = "completed"
    FAILED              = "failed"
    RETRY               = "retry"

class QCStatus(str, Enum):
    PASSED         = "passed"
    MANUAL_REVIEW  = "manual_review"
    AUTO_REJECT    = "auto_reject"

class ImageFormat(str, Enum):
    PNG  = "png"
    JPEG = "jpeg"
    WEBP = "webp"


# ─────────────────────────────────────────────────────────────────────────────
# Plattform-Format-Definitionen
# ─────────────────────────────────────────────────────────────────────────────

# (platform, content_type) → (aspect_ratio, width, height, mime_type)
PLATFORM_FORMATS: dict[tuple[str, str], tuple[str, int, int, str]] = {
    ("instagram", "reel_thumbnail"): ("9:16", 1080, 1920, "image/jpeg"),
    ("instagram", "reel"):           ("9:16", 1080, 1920, "image/jpeg"),
    ("instagram", "feed_post"):      ("1:1",  1080, 1080, "image/jpeg"),
    ("instagram", "story_image"):    ("9:16", 1080, 1920, "image/jpeg"),
    ("instagram", "carousel_slide"): ("1:1",  1080, 1080, "image/jpeg"),
    ("facebook",  "feed_post"):      ("1.91:1", 1200, 630, "image/jpeg"),
    ("facebook",  "story_image"):    ("9:16", 1080, 1920, "image/jpeg"),
    ("facebook",  "reel"):           ("9:16", 1080, 1920, "image/jpeg"),
}

DEFAULT_FORMAT = ("1:1", 1080, 1080, "image/jpeg")


def get_platform_format(platform: str, content_type: str) -> tuple[str, int, int, str]:
    """Gibt (aspect_ratio, width, height, mime_type) für Platform+ContentType zurück."""
    return PLATFORM_FORMATS.get(
        (platform.lower(), content_type.lower()),
        DEFAULT_FORMAT,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Input-Datenklassen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageGenerationRequest:
    """
    Anfrage zur Generierung eines einzelnen Bildes aus einem validierten Brief.
    Einstiegspunkt für ImageGenerationService.generate_image().
    """
    brief_uuid:   UUID
    platform:     str   # Überschreibt brief.platform wenn gesetzt
    content_type: str   # Überschreibt brief.content_type wenn gesetzt
    job_id:       str   # Vorgeneriert vom Aufrufer (IMG-YYYY-NNN-Format)
    requested_by: str   # Agent-Name oder Operator-Username

    # Optionale Overrides
    force_regenerate: bool = False   # Ignoriert vorhandenes Bild für diese brief_uuid
    quality_boost:    bool = False   # Erhöhte Qualitäts-Parameter (mehr Steps)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BuiltPrompt:
    """
    Vollständiger Prompt nach Assemblierung durch ImagePromptBuilder.
    Enthält alle Komponenten + CI-Status + Audit-Felder.
    """
    # Prompt-Texte
    positive_prompt:  str
    negative_prompt:  str

    # Format
    platform:         str
    content_type:     str
    aspect_ratio:     str
    width:            int
    height:           int
    mime_type:        str

    # Audit
    template_version:    str
    components:          dict[str, str]   # Rohwert je Komponente (SUBJECT, SETTING, …)
    ci_checks_passed:    bool
    ci_violations:       list[str]        # Leer wenn ci_checks_passed=True
    token_count_positive: int
    token_count_negative: int
    build_duration_ms:   int

    # Prompt-ID für Speicherung
    prompt_id: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Generiertes Bild (roh, aus API)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeneratedImage:
    """Rohes Bild direkt aus der Generierungs-API."""
    image_bytes:        bytes
    width:              int
    height:             int
    format:             str           # 'jpeg' | 'png'
    mime_type:          str
    model:              str           # z.B. 'gemini-2.0-flash-exp-image-generation'
    generation_time_ms: int
    cost_eur:           float
    is_mock:            bool = False
    api_response_id:    str  = ""     # ID aus API-Response (für Audit)


# ─────────────────────────────────────────────────────────────────────────────
# QC-Ergebnis
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class QCCategoryScore:
    code:    str    # z.B. 'ARTF'
    score:   float  # 0.0–10.0
    notes:   str    = ""


@dataclass
class QCHardError:
    code:                   str   # z.B. 'QC-HE-001'
    category:               str   # z.B. 'ARTF'
    message:                str
    auto_reject:            bool  = True
    prompt_change_required: bool  = True
    detected_at:            str   = ""

    def to_dict(self) -> dict:
        return {
            "code":                   self.code,
            "category":               self.category,
            "message":                self.message,
            "severity":               "hard_error",
            "auto_reject":            self.auto_reject,
            "prompt_change_required": self.prompt_change_required,
            "detected_at":            self.detected_at,
        }


@dataclass
class QCWarning:
    code:         str    # z.B. 'QC-WN-BRND-01'
    category:     str
    message:      str
    score_impact: float  # negativ, z.B. -0.7
    detected_at:  str    = ""

    def to_dict(self) -> dict:
        return {
            "code":         self.code,
            "category":     self.category,
            "message":      self.message,
            "severity":     "warning",
            "score_impact": self.score_impact,
            "detected_at":  self.detected_at,
        }


@dataclass
class QCResult:
    """
    Vollständiges QC-Ergebnis für ein generiertes Bild.
    Entspricht einem Datensatz in image_qc_results.
    """
    qc_id:        str
    asset_id:     str
    job_id:       str
    brief_uuid:   UUID
    brief_id:     str

    # Kategorie-Scores
    score_anat:   float = 0.0
    score_face:   float = 0.0
    score_hand:   float = 0.0
    score_lght:   float = 0.0
    score_comp:   float = 0.0
    score_emot:   float = 0.0
    score_fmrt:   float = 0.0
    score_brnd:   float = 0.0
    score_artf:   float = 0.0
    score_text:   float = 0.0
    score_prod:   float = 0.0
    score_item:   float = 0.0

    # Gesamtergebnis
    qc_score_total: float     = 0.0
    qc_status:      QCStatus  = QCStatus.AUTO_REJECT

    # Fehler und Warnungen
    hard_errors:  list[QCHardError]  = field(default_factory=list)
    warnings:     list[QCWarning]    = field(default_factory=list)

    # Flags
    gdpr_event_triggered:    bool = False
    auto_reject_reason:      str  = ""
    manual_review_reason:    str  = ""
    prompt_change_required:  bool = False

    # Technik
    qc_engine_version: str  = "1.0.0"
    qc_duration_ms:    int  = 0
    retry_count:       int  = 0
    is_mock:           bool = False

    created_at: datetime = field(default_factory=lambda: datetime.utcnow())

    @property
    def has_hard_error(self) -> bool:
        return len(self.hard_errors) > 0

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def is_passed(self) -> bool:
        return self.qc_status == QCStatus.PASSED

    @property
    def requires_retry(self) -> bool:
        return self.qc_status == QCStatus.AUTO_REJECT


# ─────────────────────────────────────────────────────────────────────────────
# Gespeichertes Asset
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StoredAsset:
    """
    Repräsentiert ein in Google Drive gespeichertes Bild-Asset.
    Entspricht einem Datensatz in der assets-Tabelle.
    """
    asset_id:        str
    job_id:          str
    brief_uuid:      UUID
    brief_id:        str
    prompt_id:       str
    platform:        str
    content_type:    str
    aspect_ratio:    str
    width:           int
    height:          int
    format:          str
    mime_type:       str
    file_size_bytes: int

    # Drive
    drive_file_id:   str
    drive_url:       str

    # Lokaler Temp-Pfad (während der Verarbeitung, danach None)
    local_path:      str | None

    # QC
    qc_score:        float
    qc_status:       str

    # Meta
    asset_version:   int      = 1
    created_at:      datetime = field(default_factory=lambda: datetime.utcnow())
    is_mock:         bool     = False


# ─────────────────────────────────────────────────────────────────────────────
# Job-Ergebnis
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageJobResult:
    """
    Vollständiges Ergebnis eines Image-Jobs.
    Returned von ImageJobRunner.run() und ImageGenerationService.generate_image().
    """
    job_id:        str
    brief_uuid:    UUID
    brief_id:      str
    platform:      str
    content_type:  str
    status:        ImageJobStatus

    # Ergebnisse der einzelnen Stages
    prompt:        BuiltPrompt   | None = None
    image:         GeneratedImage | None = None
    qc_result:     QCResult       | None = None
    asset:         StoredAsset    | None = None

    # Slack-Nachricht
    slack_ts:      str  = ""
    slack_channel: str  = ""

    # Kosten
    cost_eur_generation: float = 0.0
    cost_eur_qc:         float = 0.0
    cost_eur_total:      float = 0.0

    # Timing
    started_at:    datetime = field(default_factory=lambda: datetime.utcnow())
    completed_at:  datetime | None = None
    duration_ms:   int      = 0

    # Fehlerinfo
    error_message: str = ""
    retry_count:   int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == ImageJobStatus.COMPLETED

    @property
    def drive_url(self) -> str:
        return self.asset.drive_url if self.asset else ""
