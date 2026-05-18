"""
SQLAlchemy Engine + Session Factory.
Pool-Konfiguration für Production-Betrieb.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.sql import func

from app.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,     # Verbindung vor Nutzung testen
    pool_size=10,           # Basis-Pool-Größe
    max_overflow=20,        # Zusätzliche Verbindungen bei Lastspitzen
    pool_timeout=30,        # Sekunden bis Timeout beim Pool-Warten
    pool_recycle=1800,      # Verbindungen nach 30min recyceln (gegen MySQL/PgBouncer Timeouts)
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Basis-Klasse für alle ORM-Models."""
    pass


def get_db():
    """
    FastAPI-Dependency: liefert eine DB-Session und schließt sie nach dem Request.

    Verwendung:
        @router.get("/example")
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
