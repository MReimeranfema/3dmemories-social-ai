"""
Systemweite Enum-Typen — 3D Memories Social AI.
Alle Enums stammen direkt aus database/data-model.md.
Änderungen erfordern Datenbank-Migration + neue Versionsnummer.
"""

from enum import Enum


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    PINTEREST = "pinterest"
    YOUTUBE = "youtube"


class ContentType(str, Enum):
    REEL = "reel"
    CAROUSEL = "carousel"
    IMAGE = "image"
    STORY = "story"
    SHORT = "short"
    VIDEO = "video"


class JobStatus(str, Enum):
    # ── Ideen-Pfad ─────────────────────────────────────────────────────────────
    IDEA_CREATED = "IDEA_CREATED"
    IDEA_SENT_TO_SLACK = "IDEA_SENT_TO_SLACK"
    IDEA_APPROVED = "IDEA_APPROVED"
    IDEA_REJECTED = "IDEA_REJECTED"

    # ── Content-Job-Pfad ───────────────────────────────────────────────────────
    CONTENT_JOB_CREATED = "CONTENT_JOB_CREATED"
    BRIEF_CREATED = "BRIEF_CREATED"
    GENERATION_STARTED = "GENERATION_STARTED"
    IMAGE_GENERATION_STARTED = "IMAGE_GENERATION_STARTED"
    IMAGE_GENERATED = "IMAGE_GENERATED"
    VIDEO_GENERATION_STARTED = "VIDEO_GENERATION_STARTED"
    VIDEO_GENERATED = "VIDEO_GENERATED"
    QC_STARTED = "QC_STARTED"
    QC_FAILED = "QC_FAILED"
    QC_PASSED = "QC_PASSED"
    UPLOADED_TO_DRIVE = "UPLOADED_TO_DRIVE"
    LOGGED_TO_SHEETS = "LOGGED_TO_SHEETS"
    SENT_TO_SLACK_REVIEW = "SENT_TO_SLACK_REVIEW"
    APPROVED_FOR_MANUAL_PUBLISH = "APPROVED_FOR_MANUAL_PUBLISH"
    REJECTED = "REJECTED"

    # ── System-Status ──────────────────────────────────────────────────────────
    ERROR = "ERROR"
    ARCHIVED = "ARCHIVED"


class AgentName(str, Enum):
    MASTER_AGENT = "master-agent"
    IDEEN_AGENT = "ideen-agent"
    HOOK_AGENT = "hook-agent"
    TREND_AGENT = "trend-agent"
    INSTAGRAM_AGENT = "instagram-agent"
    TIKTOK_AGENT = "tiktok-agent"
    FACEBOOK_AGENT = "facebook-agent"
    PINTEREST_AGENT = "pinterest-agent"
    YOUTUBE_AGENT = "youtube-agent"
    BILD_AGENT = "bild-agent"
    VIDEO_AGENT = "video-agent"
    TEXT_AGENT = "text-agent"
    QC_AGENT = "qc-agent"
    COMPLIANCE_AGENT = "compliance-agent"
    ANALYTICS_AGENT = "analytics-agent"
    COST_CONTROL_AGENT = "cost-control-agent"
    ASSET_MANAGER = "asset-manager"
    SYSTEM_ORCHESTRATOR = "system-orchestrator"


class ApiService(str, Enum):
    GEMINI = "gemini"
    SEEDANCE = "seedance"
    SLACK = "slack"
    GOOGLE_DRIVE = "google-drive"
    GOOGLE_SHEETS = "google-sheets"
    LATER = "later"
    CLAUDE = "claude"


class AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    CAPTION = "caption"
    HASHTAGS = "hashtags"
    PROMPT_TEXT = "prompt_text"
    METADATA_JSON = "metadata_json"


class ApprovalType(str, Enum):
    IDEA_REVIEW = "idea_review"
    CONTENT_REVIEW = "content_review"


class ApprovalResult(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


class ErrorType(str, Enum):
    API_ERROR = "api_error"
    TIMEOUT = "timeout"
    GENERATION_FAILED = "generation_failed"
    QC_HARD_FAIL = "qc_hard_fail"
    BUDGET_EXCEEDED = "budget_exceeded"
    DRIVE_ERROR = "drive_error"
    SHEETS_ERROR = "sheets_error"
    INVALID_STATUS = "invalid_status"
    MISSING_INPUT = "missing_input"
    AUTH_ERROR = "auth_error"
    DATA_CORRUPTION = "data_corruption"
