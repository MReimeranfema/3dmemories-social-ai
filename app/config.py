from functools import lru_cache
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Datenbank ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://social_ai:secret@localhost:5432/social_ai_db"

    # ── Redis / Celery ─────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # ── Slack ──────────────────────────────────────────────────────────────────
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_DM_USER_ID: str = ""          # Markus' User-ID für DM-Alerts
    SLACK_CHANNEL_CONTROL: str = "#3dm-control"
    SLACK_CHANNEL_ERRORS: str = "#3dm-errors"
    AUTHORIZED_SLACK_USERS: str = ""    # Kommagetrennte User-IDs

    # ── Google ─────────────────────────────────────────────────────────────────
    GOOGLE_SERVICE_ACCOUNT_JSON: str = ""          # Pfad zur credentials.json (lokal)
    GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT: str = ""  # Roher JSON-Inhalt als Env-Var (Railway)
    GOOGLE_DRIVE_FOLDER_ID: str = ""
    GOOGLE_SHEETS_SPREADSHEET_ID: str = ""
    GOOGLE_SHEETS_TAB_JOBS: str = "CONTENT-JOBS"

    # ── Claude / Anthropic ─────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL_HAIKU: str = "claude-haiku-4-5-20251001"
    CLAUDE_MODEL_SONNET: str = "claude-sonnet-4-6"

    # ── Gemini / Nano Banana ───────────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_IMAGE_MODEL: str = "gemini-2.5-flash-image"
    GEMINI_IMAGE_COST_EUR_PER_IMAGE: float = 0.004   # ~0.004 EUR pro Bild (geschätzt)
    IMAGE_MAX_RETRIES: int = 5
    IMAGE_COST_CAP_EUR_PER_JOB: float = 0.10         # Abbruch wenn Einzeljob > 10 Cent
    IMAGE_COST_CAP_EUR_DAILY: float = 40.0            # Tages-Cap
    IMAGE_TEMP_DIR: str = "/tmp/3dm_images"           # Lokales Temp-Verzeichnis

    # ── Google Drive Bildpfade ─────────────────────────────────────────────────
    GOOGLE_DRIVE_IMAGES_FOLDER_ID: str = ""           # Root-Ordner für generierte Bilder
    GOOGLE_SHEETS_TAB_IMAGES: str = "IMAGES"
    GOOGLE_SHEETS_TAB_IMAGE_QC: str = "IMAGE_QC_LOG"

    # ── Slack Bild-Review ──────────────────────────────────────────────────────
    SLACK_CHANNEL_IMAGE_REVIEW: str = "#3dm-image-review"

    # ── Ideen-Agent ────────────────────────────────────────────────────────────
    IDEEN_AGENT_DEFAULT_COUNT: int = 10
    IDEEN_AGENT_MAX_COUNT: int = 20
    IDEEN_AGENT_DEFAULT_PLATFORM: str = "all"
    IDEEN_AGENT_DEFAULT_MODEL: str = "haiku"
    IDEEN_AGENT_EXCLUDE_DAYS: int = 30       # Ideen der letzten N Tage ausschließen
    IDEEN_AGENT_EXCLUDE_LIMIT: int = 30      # Max. ausgeschlossene Ideen im Prompt
    IDEEN_AGENT_MIN_VALID_PCT: int = 80      # Mindest-% valider Ideen nach Self-Check
    IDEEN_AGENT_IDEA_TTL_DAYS: int = 7       # Ideen verfallen nach N Tagen
    IDEEN_AGENT_REFILL_THRESHOLD: int = 2    # Neue Ideen wenn < N offen

    # ── App ────────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    APP_VERSION: str = "0.1.0"

    # ── Computed properties ────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def authorized_users_list(self) -> list[str]:
        """Gibt die Whitelist der erlaubten Slack-User-IDs zurück."""
        return [u.strip() for u in self.AUTHORIZED_SLACK_USERS.split(",") if u.strip()]

    # ── Validierung ────────────────────────────────────────────────────────────

    @field_validator("APP_ENV")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"APP_ENV muss einer von {allowed} sein, nicht '{v}'")
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if v and not v.startswith("postgresql://"):
            raise ValueError("DATABASE_URL muss mit 'postgresql://' beginnen")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"LOG_LEVEL muss einer von {allowed} sein")
        return v

    def warn_missing(self) -> list[str]:
        """
        Gibt eine Liste von Warnungen zurück wenn kritische Variablen fehlen.
        Wird beim App-Start geloggt — blockiert nicht (Dev-Mode toleriert leere Werte).
        """
        warnings = []
        if not self.ANTHROPIC_API_KEY:
            warnings.append("ANTHROPIC_API_KEY nicht gesetzt — Claude-API deaktiviert (Dev-Mode aktiv)")
        if not self.SLACK_BOT_TOKEN:
            warnings.append("SLACK_BOT_TOKEN nicht gesetzt — Slack-Integration deaktiviert")
        if not self.SLACK_SIGNING_SECRET:
            warnings.append("SLACK_SIGNING_SECRET nicht gesetzt — Webhook-Verifikation deaktiviert")
        if not self.GOOGLE_SERVICE_ACCOUNT_JSON:
            warnings.append("GOOGLE_SERVICE_ACCOUNT_JSON nicht gesetzt — Drive/Sheets deaktiviert")
        if not self.GOOGLE_DRIVE_FOLDER_ID:
            warnings.append("GOOGLE_DRIVE_FOLDER_ID nicht gesetzt")
        if not self.GOOGLE_SHEETS_SPREADSHEET_ID:
            warnings.append("GOOGLE_SHEETS_SPREADSHEET_ID nicht gesetzt")
        if not self.authorized_users_list and self.is_production:
            warnings.append("AUTHORIZED_SLACK_USERS leer — alle User sind in Production erlaubt!")
        return warnings


@lru_cache
def get_settings() -> Settings:
    """Gecachte Settings-Instanz. Einmal laden, überall verwenden."""
    return Settings()


# Direkt importierbare Singleton-Instanz
settings = get_settings()
