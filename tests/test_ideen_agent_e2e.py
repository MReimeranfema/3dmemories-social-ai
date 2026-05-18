"""
E2E Integration Test — Ideen-Agent Vollflow.

Testet den vollständigen Pfad: Generieren → Freigeben → Ablehnen → DB-Status.

Voraussetzungen (alle automatisch erfüllt ohne externe Dienste):
  - Claude:  Dev-Mode aktiv wenn ANTHROPIC_API_KEY leer → Mock-Output (N Ideen)
  - Slack:   Dev-Mode aktiv wenn SLACK_BOT_TOKEN leer   → Nachrichten werden simuliert
  - Sheets:  Dev-Mode aktiv wenn GOOGLE_SERVICE_ACCOUNT_JSON leer → Logging simuliert
  - DB:      SQLite in-memory — kein PostgreSQL nötig

Schritte pro Test-Szenario:
  1. SQLite-DB mit ideas-Tabelle aufsetzen
  2. IdeenAgent.run(count=10) → 10 Ideen in DB (Status: IDEA_CREATED)
  3. handle_approve(idea_id_0) → Status: IDEA_APPROVED, approved_by gesetzt
  4. handle_reject(idea_id_1)  → Status: IDEA_REJECTED, offene Ideen gezählt
  5. Assertions auf DB-Zustand und Rückgabewerte
"""

import re
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.idea import Idea
from app.core.enums import JobStatus
from app.agents.ideen_agent import IdeenAgent, IdeenAgentParams


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def engine():
    """SQLite in-memory Engine. Frisch pro Test — kein Zustand zwischen Tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # SQLite-FK-Enforcement ist standardmäßig deaktiviert →
        # slack_messages-Tabelle muss nicht existieren.
    )
    yield eng
    eng.dispose()


@pytest.fixture(scope="function")
def sqlite_session(engine):
    """
    SQLite-Session mit ideas-Tabelle.
    Nur ideas wird erstellt — slack_messages hat JSONB (PostgreSQL-only).
    """
    # Nur die Idea-Tabelle erstellen (keine JSONB-Abhängigkeit)
    Base.metadata.create_all(engine, tables=[Idea.__table__])

    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture(scope="function")
def agent(sqlite_session):
    """IdeenAgent mit SQLite-Session (echte DB-Writes, Mock Claude + Slack)."""
    return IdeenAgent(db=sqlite_session)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestIdeenAgentRun:

    def test_generates_10_ideas(self, agent, sqlite_session):
        """run(count=10) legt genau 10 Ideen in der DB ab."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))

        assert result.error is None, f"Agent-Fehler: {result.error}"
        assert result.ideas_requested == 10
        assert result.ideas_generated == 10
        assert result.ideas_passed_selfcheck == 10
        assert result.ideas_rejected_selfcheck == 0
        assert len(result.idea_ids) == 10
        assert result.batch_id.startswith("BATCH-")
        assert result.slack_posted is True  # Dev-Mode: simuliert, kein echter Aufruf

        # DB-Check
        db_ideas = sqlite_session.query(Idea).all()
        assert len(db_ideas) == 10
        for idea in db_ideas:
            assert idea.status == JobStatus.IDEA_CREATED
            assert idea.is_deleted is False
            assert idea.hook
            assert idea.core_emotion
            assert idea.idea_id.startswith("3DM-")

    def test_idea_id_format(self, agent, sqlite_session):
        """Idea-IDs folgen dem Format 3DM-YYYY-NNN."""
        result = agent.run(IdeenAgentParams(platform="instagram", count=5, channel="C_TEST"))
        assert result.error is None

        for idea_id in result.idea_ids:
            assert re.match(r"^3DM-\d{4}-\d{3,}$", idea_id), (
                f"Ungültiges ID-Format: {idea_id}"
            )

    def test_platform_distribution(self, agent, sqlite_session):
        """Bei platform=all: beide Plattformen vertreten (je mind. 4 von 10)."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))
        assert result.error is None

        db_ideas = sqlite_session.query(Idea).all()
        ig_count = sum(1 for i in db_ideas if "instagram" in i.best_platforms)
        fb_count = sum(1 for i in db_ideas if "facebook" in i.best_platforms)

        assert ig_count >= 4, f"Zu wenig Instagram-Ideen: {ig_count}/10"
        assert fb_count >= 4, f"Zu wenig Facebook-Ideen: {fb_count}/10"

    def test_cost_tracked(self, agent, sqlite_session):
        """Kosten werden berechnet (> 0) und Model-String ist gesetzt."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))
        assert result.error is None
        assert result.cost_eur > 0.0
        assert result.model_used  # z.B. "claude-haiku-4-5-20251001"

    def test_all_self_check_pass(self, agent, sqlite_session):
        """Alle Mock-Ideen bestehen den Self-Check — keine sollen verworfen werden."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))
        assert result.error is None
        # Mock-Templates haben keine verbotenen Wörter → alle 10 bestehen
        assert result.ideas_rejected_selfcheck == 0, (
            f"{result.ideas_rejected_selfcheck} Ideen vom Self-Check verworfen"
        )


class TestIdeenAgentApprove:

    def test_approve_updates_status(self, agent, sqlite_session):
        """handle_approve() setzt Status auf IDEA_APPROVED."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))
        assert result.error is None
        idea_id = result.idea_ids[0]

        agent.handle_approve(
            idea_id=idea_id,
            user_id="U_MARKUS",
            channel="C_TEST",
            message_ts="1748000000.000001",
        )

        sqlite_session.expire_all()  # Session-Cache leeren → fresh read aus SQLite
        idea = sqlite_session.query(Idea).filter_by(idea_id=idea_id).first()

        assert idea is not None
        assert idea.status == JobStatus.IDEA_APPROVED
        assert idea.approved_by == "U_MARKUS"
        assert idea.approved_at is not None

    def test_approve_does_not_affect_others(self, agent, sqlite_session):
        """Freigabe einer Idee ändert den Status der anderen nicht."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))
        idea_id = result.idea_ids[0]

        agent.handle_approve(idea_id=idea_id, user_id="U_MARKUS",
                             channel="C_TEST", message_ts="1748000000.000001")

        sqlite_session.expire_all()
        untouched = sqlite_session.query(Idea).filter(Idea.idea_id != idea_id).all()
        assert all(i.status == JobStatus.IDEA_CREATED for i in untouched), (
            "Freigabe hat andere Ideen verändert"
        )


class TestIdeenAgentReject:

    def test_reject_updates_status(self, agent, sqlite_session):
        """handle_reject() setzt Status auf IDEA_REJECTED."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))
        assert result.error is None
        idea_id = result.idea_ids[1]

        open_count = agent.handle_reject(
            idea_id=idea_id,
            user_id="U_MARKUS",
            channel="C_TEST",
            message_ts="1748000000.000002",
        )

        sqlite_session.expire_all()
        idea = sqlite_session.query(Idea).filter_by(idea_id=idea_id).first()

        assert idea.status == JobStatus.IDEA_REJECTED
        assert open_count == 9  # 10 generiert - 1 abgelehnt

    def test_reject_returns_correct_open_count(self, agent, sqlite_session):
        """open_count = generierte Ideen - abgelehnte Ideen."""
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))

        # Zwei Ideen ablehnen
        agent.handle_reject(result.idea_ids[0], "U_MARKUS", "C_TEST", "1748000000.000001")
        open_count = agent.handle_reject(result.idea_ids[1], "U_MARKUS", "C_TEST", "1748000000.000002")

        assert open_count == 8  # 10 - 2 = 8


class TestIdeenAgentFullFlow:

    def test_generate_approve_reject_db_state(self, agent, sqlite_session):
        """
        Vollständiger Flow:
          10 Ideen generieren → 1 freigeben → 1 ablehnen → 8 offen.

        Dieser Test ist der Kern des E2E-Tests und validiert alle 6 Schritte:
          1. 10 Ideen in DB (IDEA_CREATED)
          2. Button [FREIGEBEN] → status=IDEA_APPROVED
          3. DB: approved_by und approved_at gesetzt
          4. Button [ABLEHNEN] → status=IDEA_REJECTED
          5. Slack update_message simuliert (kein echter Call, kein Fehler)
          6. open_count korrekt zurückgegeben
        """
        # ── Schritt 1: Generieren ──────────────────────────────────────────────
        result = agent.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))

        assert result.error is None
        assert result.ideas_passed_selfcheck == 10
        assert len(result.idea_ids) == 10

        db_count = sqlite_session.query(Idea).count()
        assert db_count == 10

        id_approve = result.idea_ids[0]
        id_reject = result.idea_ids[1]

        # ── Schritt 2+3: Freigeben ─────────────────────────────────────────────
        agent.handle_approve(
            idea_id=id_approve,
            user_id="U_MARKUS",
            channel="C_TEST",
            message_ts="1748000000.000001",
        )

        # ── Schritt 4: Ablehnen ────────────────────────────────────────────────
        open_count = agent.handle_reject(
            idea_id=id_reject,
            user_id="U_MARKUS",
            channel="C_TEST",
            message_ts="1748000000.000002",
        )

        # ── Schritt 5+6: DB-Zustand verifizieren ──────────────────────────────
        sqlite_session.expire_all()
        all_ideas = sqlite_session.query(Idea).all()
        status_map = {i.idea_id: i.status for i in all_ideas}

        assert status_map[id_approve] == JobStatus.IDEA_APPROVED
        assert status_map[id_reject] == JobStatus.IDEA_REJECTED

        open_in_db = sum(1 for s in status_map.values() if s == JobStatus.IDEA_CREATED)
        assert open_in_db == 8, f"Erwartet 8 offene Ideen, gefunden: {open_in_db}"
        assert open_count == 8, f"handle_reject gab {open_count} zurück, erwartet 8"

        # ── Approved-Details ───────────────────────────────────────────────────
        approved_idea = sqlite_session.query(Idea).filter_by(idea_id=id_approve).first()
        assert approved_idea.approved_by == "U_MARKUS"
        assert approved_idea.approved_at is not None


# ── Standalone-Ausführung (ohne pytest) ───────────────────────────────────────

if __name__ == "__main__":
    """
    Schnelltest ohne pytest:
      cd 3dmemories-social-ai
      python -m tests.test_ideen_agent_e2e
    """
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, tables=[Idea.__table__])
    Session = sessionmaker(bind=eng)
    session = Session()

    ag = IdeenAgent(db=session)
    result = ag.run(IdeenAgentParams(platform="all", count=10, channel="C_TEST"))

    print(f"\n{'='*65}")
    print(f"BATCH-ID:      {result.batch_id}")
    print(f"Generiert:     {result.ideas_generated}")
    print(f"Self-Check OK: {result.ideas_passed_selfcheck}")
    print(f"Verworfen:     {result.ideas_rejected_selfcheck}")
    print(f"Kosten:        {result.cost_eur:.5f} EUR")
    print(f"Slack:         {'simuliert (dev-mode)' if result.slack_posted else 'FEHLER'}")
    print(f"Fehler:        {result.error or 'keiner'}")
    print(f"{'─'*65}")

    id_approve = result.idea_ids[0]
    id_reject  = result.idea_ids[1]

    ag.handle_approve(id_approve, "U_MARKUS", "C_TEST", "1748000000.000001")
    open_count = ag.handle_reject(id_reject, "U_MARKUS", "C_TEST", "1748000000.000002")

    session.expire_all()
    all_ideas = session.query(Idea).all()
    status_map = {i.idea_id: i.status for i in all_ideas}

    print(f"Freigegeben:   {id_approve} → {status_map[id_approve]}")
    print(f"Abgelehnt:     {id_reject}  → {status_map[id_reject]}")
    print(f"Offen:         {open_count}")
    print(f"{'='*65}")

    assert status_map[id_approve] == JobStatus.IDEA_APPROVED
    assert status_map[id_reject]  == JobStatus.IDEA_REJECTED
    assert open_count == 8
    print("ALLE ASSERTIONS BESTANDEN\n")

    session.close()
    eng.dispose()
