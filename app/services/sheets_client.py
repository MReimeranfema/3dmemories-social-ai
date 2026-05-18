"""
Google Sheets Logging-Client.
Spezifikation: api/api-gateway.md → API 4: Google Sheets API

Service Account Auth — kein OAuth-Flow.

Aufgaben:
  1. ensure_header()         — Header-Zeile anlegen falls Tab leer
  2. append_job_row(data)    — Neue Zeile anhängen, Zeilennummer zurückgeben
  3. update_job_row(row, data) — Bestehende Zeile aktualisieren (Status-Updates)

Rate-Limit: 100 Writes/Tag (Google Sheets Free Tier API).
  Phase 1: In-Memory-Counter mit täglichem Reset (kein Redis nötig).
  Phase 2: Redis-Counter wenn Worker-Instanzen sich teilen müssen.

Formelschutz: Zellen die mit "=" beginnen werden NIEMALS überschrieben.
  Gilt für alle Update-Operationen — schützt berechnete Spalten im Sheet.

Non-blocking bei Fehler: Sheets-Fehler stoppen NICHT den Job.
  Bei Fehler: Loggen + Exception weiterwerfen — Caller entscheidet ob Retry.
  Spezifikation: "Bei Sheets-Fehler: Job läuft weiter — kein Hard Stop."

Dev-Mode: Wenn GOOGLE_SERVICE_ACCOUNT_JSON leer → kein API-Call, nur loggen.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

# ── Konstanten ─────────────────────────────────────────────────────────────────

# Maximale Writes pro Tag (Google Sheets Free Tier)
DAILY_WRITE_LIMIT = 100

# Header-Zeile des Content-Jobs-Tabs
# Reihenfolge entspricht der Spalten-Reihenfolge im Sheet.
JOBS_HEADER = [
    "content_id",          # A — Primärschlüssel
    "platform",            # B
    "status",              # C
    "idea_title",          # D
    "created_at",          # E
    "updated_at",          # F
    "drive_folder_url",    # G
    "primary_asset_url",   # H
    "caption_preview",     # I — erste 100 Zeichen der Caption
    "hashtags",            # J
    "qc_score",            # K
    "approved_by",         # L
    "published_at",        # M
    "error_message",       # N
    "notes",               # O
]

# Spaltenindex (0-basiert) für schnellen Lookup
COL_INDEX = {name: idx for idx, name in enumerate(JOBS_HEADER)}


# ── Exceptions ─────────────────────────────────────────────────────────────────

class SheetsError(Exception):
    """Fehler beim Zugriff auf Google Sheets."""

class SheetsRateLimitError(SheetsError):
    """Tägliches Write-Limit erreicht."""

class SheetsAuthError(SheetsError):
    """Service-Account-Authentifizierung fehlgeschlagen."""


# ── Rate-Limit-Counter (In-Memory, Phase 1) ────────────────────────────────────

class _DailyWriteCounter:
    """
    Einfacher In-Memory-Counter für tägliche Write-Limits.

    Nicht thread-safe für parallele Schreibzugriffe — für Phase 1
    (Single-Worker) ausreichend. Phase 2: Redis-basiert ersetzen.
    """

    def __init__(self) -> None:
        self._count: int = 0
        self._date: date = date.today()

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._date:
            log.info(
                "sheets.rate_limit.daily_reset",
                previous_count=self._count,
                previous_date=str(self._date),
            )
            self._count = 0
            self._date = today

    def check_and_increment(self) -> None:
        """
        Prüft ob das Limit erreicht ist und zählt hoch.
        Wirft SheetsRateLimitError wenn Limit erreicht.
        """
        self._reset_if_new_day()
        if self._count >= DAILY_WRITE_LIMIT:
            log.error(
                "sheets.rate_limit.exceeded",
                count=self._count,
                limit=DAILY_WRITE_LIMIT,
                date=str(self._date),
            )
            raise SheetsRateLimitError(
                f"Tägliches Sheets-Write-Limit ({DAILY_WRITE_LIMIT}) erreicht. "
                f"Bisherige Writes heute: {self._count}"
            )
        self._count += 1
        log.debug(
            "sheets.rate_limit.write",
            count=self._count,
            remaining=DAILY_WRITE_LIMIT - self._count,
        )

    @property
    def count(self) -> int:
        self._reset_if_new_day()
        return self._count

    @property
    def remaining(self) -> int:
        self._reset_if_new_day()
        return max(0, DAILY_WRITE_LIMIT - self._count)


# ── SheetsClient ───────────────────────────────────────────────────────────────

class SheetsClient:
    """
    Google Sheets API Wrapper — Service Account.

    Verwendet spreadsheets.values.append / .update / .get.
    Tab-Name: settings.GOOGLE_SHEETS_TAB_JOBS (Default: "CONTENT-JOBS")

    Im Dev-Mode (GOOGLE_SERVICE_ACCOUNT_JSON leer):
      Alle Methoden loggen was sie schreiben würden.
      append_job_row gibt simulierte Zeilennummer zurück (2 + counter).
      Keine echten API-Calls.
    """

    def __init__(self) -> None:
        self._service: Any = None          # googleapiclient.discovery.Resource
        self._dev_mode = (
            not settings.GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT
            and not settings.GOOGLE_SERVICE_ACCOUNT_JSON
        )
        self._rate_counter = _DailyWriteCounter()
        self._dev_row_counter = 2          # Zeile 1 = Header → erste Datenzeile = 2

        if self._dev_mode:
            log.warning(
                "sheets_client.dev_mode",
                reason="Keine Google-Credentials gesetzt — alle Writes simuliert",
            )
        else:
            self._service = self._build_service()

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def ensure_header(self) -> None:
        """
        Prüft ob Zeile 1 des Tabs leer ist — legt Header an falls ja.
        Sicher aufzurufen beim App-Start oder vor dem ersten Write.

        Kein Write-Counter-Verbrauch wenn Header bereits vorhanden.
        """
        tab = settings.GOOGLE_SHEETS_TAB_JOBS
        range_a1 = f"'{tab}'!A1:Z1"

        if self._dev_mode:
            log.info(
                "sheets.ensure_header.simulated",
                tab=tab,
                header_columns=len(JOBS_HEADER),
                columns=JOBS_HEADER,
            )
            return

        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID, range=range_a1)
                .execute()
            )
            existing = result.get("values", [[]])[0] if result.get("values") else []

            if existing:
                log.debug("sheets.ensure_header.already_set", tab=tab, columns=len(existing))
                return

            # Header schreiben — zählt als ein Write
            self._rate_counter.check_and_increment()
            self._service.spreadsheets().values().update(
                spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                range=f"'{tab}'!A1",
                valueInputOption="RAW",
                body={"values": [JOBS_HEADER]},
            ).execute()
            log.info("sheets.ensure_header.written", tab=tab, columns=len(JOBS_HEADER))

        except SheetsRateLimitError:
            raise
        except Exception as exc:
            log.error("sheets.ensure_header.error", tab=tab, error=str(exc))
            raise SheetsError(f"Header-Check fehlgeschlagen: {exc}") from exc

    def append_job_row(self, data: dict[str, Any]) -> int:
        """
        Hängt einen neuen Content-Job als Zeile ans Sheet an.

        Args:
            data: Dict mit Job-Daten. Keys entsprechen JOBS_HEADER-Spaltennamen.
                  Fehlende Keys → leere Zelle. Unbekannte Keys werden ignoriert.

        Returns:
            Zeilennummer (1-basiert) der neu angelegten Zeile.

        Raises:
            SheetsRateLimitError: Tägliches Limit erschöpft
            SheetsError:          API-Fehler
        """
        tab = settings.GOOGLE_SHEETS_TAB_JOBS
        row_values = self._build_row(data)

        if self._dev_mode:
            row_num = self._dev_row_counter
            self._dev_row_counter += 1
            log.info(
                "sheets.append_job_row.simulated",
                tab=tab,
                row=row_num,
                content_id=data.get("content_id", "?"),
                status=data.get("status", "?"),
                values=row_values,
            )
            return row_num

        self._rate_counter.check_and_increment()

        try:
            result = (
                self._service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                    range=f"'{tab}'!A:A",
                    valueInputOption="USER_ENTERED",  # erlaubt Datumsformatierung
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row_values]},
                )
                .execute()
            )

            # Zeilennummer aus Range-Response extrahieren: "'CONTENT-JOBS'!A42:O42" → 42
            updated_range = result.get("updates", {}).get("updatedRange", "")
            row_num = self._extract_row_number(updated_range)

            log.info(
                "sheets.append_job_row.ok",
                tab=tab,
                row=row_num,
                content_id=data.get("content_id", "?"),
                status=data.get("status", "?"),
                writes_remaining=self._rate_counter.remaining,
            )
            return row_num

        except SheetsRateLimitError:
            raise
        except Exception as exc:
            log.error(
                "sheets.append_job_row.error",
                tab=tab,
                content_id=data.get("content_id", "?"),
                error=str(exc),
            )
            raise SheetsError(f"append_job_row fehlgeschlagen: {exc}") from exc

    def update_job_row(self, row: int, data: dict[str, Any]) -> None:
        """
        Aktualisiert eine bestehende Job-Zeile.

        Formelschutz: Zellen die mit "=" beginnen werden übersprungen.
        Nur explizit übergebene Keys werden aktualisiert (keine Leer-Überschreibung).

        Args:
            row:  Zeilennummer (1-basiert) der zu aktualisierenden Zeile
            data: Dict mit zu aktualisierenden Feldern (Partial-Update möglich)

        Raises:
            SheetsRateLimitError: Tägliches Limit erschöpft
            SheetsError:          API-Fehler
        """
        tab = settings.GOOGLE_SHEETS_TAB_JOBS

        if self._dev_mode:
            safe_data = self._apply_formula_protection(data)
            log.info(
                "sheets.update_job_row.simulated",
                tab=tab,
                row=row,
                content_id=data.get("content_id", "?"),
                status=data.get("status", "?"),
                updated_fields=list(safe_data.keys()),
            )
            return

        # Aktuelle Zeile lesen für Formelschutz (kostet kein Write-Budget)
        range_a1 = f"'{tab}'!A{row}:{self._col_letter(len(JOBS_HEADER))}{row}"
        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                    range=range_a1,
                )
                .execute()
            )
            current_row = result.get("values", [[]])[0] if result.get("values") else []
        except Exception as exc:
            log.error("sheets.update_job_row.read_error", row=row, error=str(exc))
            raise SheetsError(f"Lesen vor Update fehlgeschlagen (Zeile {row}): {exc}") from exc

        # Neuen Zeileninhalt bauen (mit Formelschutz)
        new_row = list(current_row) + [""] * (len(JOBS_HEADER) - len(current_row))  # auffüllen

        for key, value in data.items():
            if key not in COL_INDEX:
                continue
            col_idx = COL_INDEX[key]
            current_value = new_row[col_idx] if col_idx < len(new_row) else ""
            if isinstance(current_value, str) and current_value.startswith("="):
                log.debug(
                    "sheets.update_job_row.formula_protected",
                    row=row,
                    column=key,
                    col_idx=col_idx,
                )
                continue  # Formel nicht überschreiben
            new_row[col_idx] = self._format_value(value)

        self._rate_counter.check_and_increment()

        try:
            self._service.spreadsheets().values().update(
                spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                body={"values": [new_row]},
            ).execute()

            log.info(
                "sheets.update_job_row.ok",
                tab=tab,
                row=row,
                content_id=data.get("content_id", "?"),
                status=data.get("status", "?"),
                writes_remaining=self._rate_counter.remaining,
            )

        except SheetsRateLimitError:
            raise
        except Exception as exc:
            log.error(
                "sheets.update_job_row.error",
                tab=tab,
                row=row,
                content_id=data.get("content_id", "?"),
                error=str(exc),
            )
            raise SheetsError(f"update_job_row fehlgeschlagen (Zeile {row}): {exc}") from exc

    @property
    def writes_today(self) -> int:
        """Anzahl Writes heute (für /system-status Command)."""
        return self._rate_counter.count

    @property
    def writes_remaining(self) -> int:
        """Verbleibende Writes heute."""
        return self._rate_counter.remaining

    # ── Interne Hilfsmethoden ──────────────────────────────────────────────────

    def _build_service(self) -> Any:
        """
        Baut den Google Sheets API Service via Service Account.

        Credential-Quellen (Priorität):
          1. GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT — roher JSON-String (Railway)
          2. GOOGLE_SERVICE_ACCOUNT_JSON          — Dateipfad (lokal)
        """
        try:
            import json
            from google.oauth2 import service_account  # type: ignore
            from googleapiclient.discovery import build  # type: ignore

            scopes = ["https://www.googleapis.com/auth/spreadsheets"]

            if settings.GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT:
                info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT)
                creds = service_account.Credentials.from_service_account_info(
                    info, scopes=scopes
                )
                log.info(
                    "sheets_client.authenticated",
                    source="env_var_content",
                    spreadsheet_id=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                )
            else:
                creds = service_account.Credentials.from_service_account_file(
                    settings.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes
                )
                log.info(
                    "sheets_client.authenticated",
                    source="file",
                    path=settings.GOOGLE_SERVICE_ACCOUNT_JSON,
                    spreadsheet_id=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                )

            return build("sheets", "v4", credentials=creds, cache_discovery=False)

        except FileNotFoundError as exc:
            raise SheetsAuthError(f"Service-Account-Datei nicht gefunden: {exc}") from exc
        except Exception as exc:
            log.error("sheets_client.auth_error", error=str(exc))
            raise SheetsAuthError(f"Sheets-Auth fehlgeschlagen: {exc}") from exc

    def _build_row(self, data: dict[str, Any]) -> list[str]:
        """Baut eine vollständige Zeile (alle JOBS_HEADER-Spalten) aus data."""
        return [
            self._format_value(data.get(col, ""))
            for col in JOBS_HEADER
        ]

    def _apply_formula_protection(self, data: dict[str, Any]) -> dict[str, Any]:
        """Filtert Keys aus data die auf Formel-Spalten zeigen (Dev-Mode Logging)."""
        return {k: v for k, v in data.items() if k in COL_INDEX}

    @staticmethod
    def _format_value(value: Any) -> str:
        """
        Konvertiert Python-Typen in Sheets-kompatible Strings.
        Datumsfelder als ISO-Format, None als leer, Rest als str().
        """
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, float):
            return f"{value:.4f}" if value != int(value) else str(int(value))
        return str(value)

    @staticmethod
    def _col_letter(col_index: int) -> str:
        """Konvertiert 0-basierten Spaltenindex in Buchstaben (0→A, 25→Z, 26→AA)."""
        result = ""
        col_index += 1  # 1-basiert für Modulo-Arithmetik
        while col_index > 0:
            col_index -= 1
            result = chr(ord("A") + col_index % 26) + result
            col_index //= 26
        return result

    @staticmethod
    def _extract_row_number(updated_range: str) -> int:
        """
        Extrahiert Zeilennummer aus Range-String wie "'CONTENT-JOBS'!A42:O42".
        Fallback: 0 wenn kein Match.
        """
        import re
        match = re.search(r"!(?:[A-Z]+)(\d+)", updated_range)
        return int(match.group(1)) if match else 0


# ── Singleton ──────────────────────────────────────────────────────────────────

_sheets_client: SheetsClient | None = None


def get_sheets_client() -> SheetsClient:
    """
    Gibt die gecachte SheetsClient-Instanz zurück.
    Lazy-Initialisierung — erst beim ersten Aufruf gebaut.
    """
    global _sheets_client
    if _sheets_client is None:
        _sheets_client = SheetsClient()
    return _sheets_client
