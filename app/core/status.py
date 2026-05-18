"""
State Machine — 3D Memories Social AI.
Verbindliche Statusübergänge aus workflows/status-machine.md.

REGEL 5:  ERROR darf aus jedem Status gesetzt werden.
REGEL 6:  ARCHIVED ist Endstatus — kein weiterer Wechsel möglich.
REGEL 7:  Statuswechsel nur durch den System-Orchestrator.
"""

from app.core.enums import JobStatus

# ── Erlaubte Übergänge ─────────────────────────────────────────────────────────
# ERROR → situationsabhängig nach Fehlertyp (in Orchestrator-Logik gesteuert).
# Minimaler Fallback: ERROR → ARCHIVED (nach max. Retries).

ALLOWED_TRANSITIONS: dict[JobStatus, list[JobStatus]] = {

    # Ideen-Pfad
    JobStatus.IDEA_CREATED: [
        JobStatus.IDEA_SENT_TO_SLACK,
        JobStatus.ERROR,
    ],
    JobStatus.IDEA_SENT_TO_SLACK: [
        JobStatus.IDEA_APPROVED,
        JobStatus.IDEA_REJECTED,
        JobStatus.ERROR,
    ],
    JobStatus.IDEA_APPROVED: [
        JobStatus.CONTENT_JOB_CREATED,
        JobStatus.ERROR,
    ],
    JobStatus.IDEA_REJECTED: [
        JobStatus.ARCHIVED,
    ],

    # Content-Job-Pfad
    JobStatus.CONTENT_JOB_CREATED: [
        JobStatus.BRIEF_CREATED,
        JobStatus.ERROR,
    ],
    JobStatus.BRIEF_CREATED: [
        JobStatus.GENERATION_STARTED,
        JobStatus.ERROR,
    ],
    JobStatus.GENERATION_STARTED: [
        JobStatus.IMAGE_GENERATION_STARTED,
        JobStatus.VIDEO_GENERATION_STARTED,
        JobStatus.QC_STARTED,           # wenn kein Bild/Video benötigt
        JobStatus.ERROR,
    ],
    JobStatus.IMAGE_GENERATION_STARTED: [
        JobStatus.IMAGE_GENERATED,
        JobStatus.ERROR,
    ],
    JobStatus.IMAGE_GENERATED: [
        JobStatus.VIDEO_GENERATION_STARTED,
        JobStatus.QC_STARTED,           # wenn kein Video benötigt
        JobStatus.ERROR,
    ],
    JobStatus.VIDEO_GENERATION_STARTED: [
        JobStatus.VIDEO_GENERATED,
        JobStatus.ERROR,
    ],
    JobStatus.VIDEO_GENERATED: [
        JobStatus.QC_STARTED,
        JobStatus.ERROR,
    ],
    JobStatus.QC_STARTED: [
        JobStatus.QC_PASSED,
        JobStatus.QC_FAILED,
        JobStatus.ERROR,
    ],
    JobStatus.QC_FAILED: [
        JobStatus.GENERATION_STARTED,   # Revision: retry_counter < 3
        JobStatus.ARCHIVED,             # max. Retries oder No-Go Hard-Reject
        JobStatus.ERROR,
    ],
    JobStatus.QC_PASSED: [
        JobStatus.UPLOADED_TO_DRIVE,
        JobStatus.ERROR,
    ],
    JobStatus.UPLOADED_TO_DRIVE: [
        JobStatus.LOGGED_TO_SHEETS,
        JobStatus.ERROR,
    ],
    JobStatus.LOGGED_TO_SHEETS: [
        JobStatus.SENT_TO_SLACK_REVIEW,
        JobStatus.ERROR,
    ],
    JobStatus.SENT_TO_SLACK_REVIEW: [
        JobStatus.APPROVED_FOR_MANUAL_PUBLISH,
        JobStatus.REJECTED,
        JobStatus.GENERATION_STARTED,   # Revision via Slack
        JobStatus.ERROR,
    ],
    JobStatus.APPROVED_FOR_MANUAL_PUBLISH: [
        JobStatus.ARCHIVED,
        JobStatus.ERROR,
    ],
    JobStatus.REJECTED: [
        JobStatus.ARCHIVED,
    ],

    # System-Status
    # ERROR → ARCHIVED als sicherer Fallback; Orchestrator kann bei Retry
    # direkt in den vorherigen Status zurück (außerhalb dieser Map gesteuert).
    JobStatus.ERROR: [
        JobStatus.ARCHIVED,
    ],
    # ARCHIVED = Endstatus (leere Liste = keine weiteren Übergänge)
    JobStatus.ARCHIVED: [],
}


def is_transition_allowed(current: JobStatus, next_status: JobStatus) -> bool:
    """
    Prüft ob ein Statuswechsel erlaubt ist.

    Sonderregeln aus status-machine.md:
    - ARCHIVED → kein weiterer Wechsel (REGEL 6)
    - ERROR kann aus jedem Status gesetzt werden (REGEL 5)
    - Alle anderen Wechsel müssen in ALLOWED_TRANSITIONS definiert sein
    """
    # Endstatus — kein Wechsel möglich
    if current == JobStatus.ARCHIVED:
        return False

    # ERROR kann immer gesetzt werden (Regel 5)
    if next_status == JobStatus.ERROR:
        return True

    return next_status in ALLOWED_TRANSITIONS.get(current, [])


def validate_transition(current: JobStatus, next_status: JobStatus) -> None:
    """
    Wie is_transition_allowed(), wirft aber InvalidStatusTransitionError
    statt bool zurückzugeben. Für Orchestrator-Verwendung.
    """
    from app.core.errors import InvalidStatusTransitionError

    if not is_transition_allowed(current, next_status):
        raise InvalidStatusTransitionError(
            f"Ungültiger Statuswechsel: {current.value} → {next_status.value}"
        )
