"""
Models-Paket — alle SQLAlchemy ORM-Modelle.

Import-Reihenfolge ist relevant für Alembic autogenerate:
Tabellen ohne FK-Abhängigkeiten zuerst, dann abhängige Tabellen.
Zirkuläre FKs (use_alter=True) werden nach Tabellenerstellung hinzugefügt.
"""

# Reihenfolge: keine Abhängigkeiten zuerst
from app.models.slack_message import SlackMessage   # noqa — kein FK zu anderen Models
from app.models.idea import Idea                    # noqa — FK → slack_messages
from app.models.content_job import ContentJob       # noqa — FK → ideas; deferred → assets
from app.models.prompt import Prompt                # noqa — FK → content_jobs; deferred → assets
from app.models.asset import Asset                  # noqa — FK → content_jobs, prompts; deferred → drive_files
from app.models.drive_file import DriveFile         # noqa — FK → content_jobs; deferred → assets
from app.models.approval import Approval            # noqa — FK → slack_messages
from app.models.api_call import ApiCall             # noqa — FK → content_jobs, prompts
from app.models.cost import Cost                    # noqa — FK → api_calls, content_jobs
from app.models.error import Error                  # noqa — FK → api_calls, slack_messages
from app.models.sheet_log import SheetLog           # noqa — keine FK (polymorphisch)
from app.models.performance import PerformanceLater # noqa — FK → content_jobs

__all__ = [
    "SlackMessage",
    "Idea",
    "ContentJob",
    "Prompt",
    "Asset",
    "DriveFile",
    "Approval",
    "ApiCall",
    "Cost",
    "Error",
    "SheetLog",
    "PerformanceLater",
]