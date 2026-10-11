from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings
from .timekeeping import now_local


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        # Capture the selected durability for this engine. Existing pooled
        # connections keep their current mode until the application restarts.
        from .operability import synchronous_mode
        durability = synchronous_mode()
        eng._hader_synchronous = durability
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=" + eng._hader_synchronous)
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def reload_durability() -> None:
    """Called only with HTTP listeners stopped, after restore/configuration reload."""
    from .operability import synchronous_mode
    engine.dispose()
    if engine.dialect.name == "sqlite":
        engine._hader_synchronous = synchronous_mode()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now() -> datetime:
    """Local wall-clock time without tzinfo (devices report local time)."""
    return now_local()
