-- ============================================================
-- Migration 003: Content Brief Validation Storage
-- Stand: 2026-05-17
-- Abhängigkeit: Migration 002 (content_briefs existiert)
-- ============================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────
-- 1. ENUM-Typen
-- ─────────────────────────────────────────────────────────────

CREATE TYPE brief_validation_result AS ENUM (
    'pending',
    'passed',
    'warning',
    'failed'
);

-- ─────────────────────────────────────────────────────────────
-- 2. content_briefs erweitern — aktueller Validierungsstand
-- ─────────────────────────────────────────────────────────────

ALTER TABLE content_briefs
    ADD COLUMN IF NOT EXISTS validation_score      DECIMAL(4,2)
        CHECK (validation_score BETWEEN 0 AND 10),

    ADD COLUMN IF NOT EXISTS validation_status     brief_validation_result
        NOT NULL DEFAULT 'pending',

    ADD COLUMN IF NOT EXISTS validation_notes      TEXT,

    ADD COLUMN IF NOT EXISTS validation_errors     JSONB
        NOT NULL DEFAULT '[]'::jsonb,

    ADD COLUMN IF NOT EXISTS validation_warnings   JSONB
        NOT NULL DEFAULT '[]'::jsonb,

    ADD COLUMN IF NOT EXISTS validated_by          VARCHAR(100),

    ADD COLUMN IF NOT EXISTS validated_at          TIMESTAMPTZ;

-- Kommentare
COMMENT ON COLUMN content_briefs.validation_score IS
    'Gesamtqualität 0–10. passed ≥8.0, warning 6.5–7.99, failed <6.5';
COMMENT ON COLUMN content_briefs.validation_status IS
    'Aktueller Validierungsstatus: pending | passed | warning | failed';
COMMENT ON COLUMN content_briefs.validation_errors IS
    'JSON-Array harter Fehler. Leer wenn keine Fehler.';
COMMENT ON COLUMN content_briefs.validation_warnings IS
    'JSON-Array weicher Warnungen. Leer wenn keine Warnungen.';

-- ─────────────────────────────────────────────────────────────
-- 3. Neue Tabelle: content_brief_validations (vollständige Historie)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE content_brief_validations (
    -- Identifikation
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    brief_id                    VARCHAR(20)     NOT NULL,
    brief_version               INT             NOT NULL,
    brief_uuid                  UUID            NOT NULL REFERENCES content_briefs(brief_uuid),

    -- Gesamt-Ergebnis
    validation_score            DECIMAL(4,2)    NOT NULL CHECK (validation_score BETWEEN 0 AND 10),
    validation_status           brief_validation_result NOT NULL,
    validation_notes            TEXT,

    -- Kategorie-Scores (0–10, aus content-brief-validation.md §10 Kategorien)
    hook_score                  DECIMAL(4,2)    CHECK (hook_score BETWEEN 0 AND 10),
    story_score                 DECIMAL(4,2)    CHECK (story_score BETWEEN 0 AND 10),
    platform_fit_score          DECIMAL(4,2)    CHECK (platform_fit_score BETWEEN 0 AND 10),
    brand_fit_score             DECIMAL(4,2)    CHECK (brand_fit_score BETWEEN 0 AND 10),
    clarity_score               DECIMAL(4,2)    CHECK (clarity_score BETWEEN 0 AND 10),
    production_realism_score    DECIMAL(4,2)    CHECK (production_realism_score BETWEEN 0 AND 10),
    risk_score                  DECIMAL(4,2)    CHECK (risk_score BETWEEN 0 AND 10),

    -- Fehler und Warnungen
    validation_errors           JSONB           NOT NULL DEFAULT '[]'::jsonb,
    validation_warnings         JSONB           NOT NULL DEFAULT '[]'::jsonb,

    -- Metadaten
    validated_by                VARCHAR(100)    NOT NULL,
    validator_version           VARCHAR(20)     NOT NULL DEFAULT '1.0.0',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE content_brief_validations IS
    'Vollständige Validierungshistorie. Jede Validierung erzeugt einen neuen Eintrag. '
    'content_briefs enthält immer den aktuellen Stand (letzten Eintrag dieser Tabelle).';

-- ─────────────────────────────────────────────────────────────
-- 4. Indexes: content_brief_validations
-- ─────────────────────────────────────────────────────────────

CREATE INDEX idx_cbv_brief_id
    ON content_brief_validations (brief_id);

CREATE INDEX idx_cbv_brief_uuid
    ON content_brief_validations (brief_uuid);

CREATE INDEX idx_cbv_status
    ON content_brief_validations (validation_status);

CREATE INDEX idx_cbv_score
    ON content_brief_validations (validation_score DESC);

CREATE INDEX idx_cbv_created_at
    ON content_brief_validations (created_at DESC);

CREATE INDEX idx_cbv_validated_by
    ON content_brief_validations (validated_by);

-- Composite: Schnellzugriff auf letzte Validierung eines Briefs
CREATE INDEX idx_cbv_brief_latest
    ON content_brief_validations (brief_uuid, created_at DESC);

-- GIN für JSONB-Suche in Fehlerlisten
CREATE INDEX idx_cbv_errors_gin
    ON content_brief_validations USING GIN (validation_errors jsonb_path_ops);

CREATE INDEX idx_cbv_warnings_gin
    ON content_brief_validations USING GIN (validation_warnings jsonb_path_ops);

-- ─────────────────────────────────────────────────────────────
-- 5. Trigger: content_briefs.validation_* automatisch sync
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION sync_brief_validation_status()
RETURNS TRIGGER AS $$
BEGIN
    -- Nach jedem INSERT in content_brief_validations den
    -- aktuellen Stand in content_briefs aktualisieren
    UPDATE content_briefs
    SET validation_score    = NEW.validation_score,
        validation_status   = NEW.validation_status,
        validation_notes    = NEW.validation_notes,
        validation_errors   = NEW.validation_errors,
        validation_warnings = NEW.validation_warnings,
        validated_by        = NEW.validated_by,
        validated_at        = NEW.created_at,
        -- production_blocked: false wenn passed oder warning, true wenn failed/pending
        production_blocked  = CASE
            WHEN NEW.validation_status IN ('passed', 'warning') THEN FALSE
            ELSE TRUE
        END
    WHERE brief_uuid = NEW.brief_uuid;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_validation_to_brief
    AFTER INSERT ON content_brief_validations
    FOR EACH ROW
    EXECUTE FUNCTION sync_brief_validation_status();

-- ─────────────────────────────────────────────────────────────
-- 6. Google Sheets Spalten-Erweiterung (via sheet_logs Konvention)
-- ─────────────────────────────────────────────────────────────
-- Spalten die in Tab CONTENT_BRIEFS ergänzt werden müssen:
-- validation_score | validation_status | validation_notes |
-- validation_errors | validation_warnings | validated_at
-- (Schreibung erfolgt durch sheets-logging Service — kein SQL notwendig)

COMMIT;
