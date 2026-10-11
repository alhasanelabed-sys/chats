"""Device data events become visible only after their database transaction commits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from hader import events, models as m
from hader.adms import server, sync
from hader.adms.protocol import AttRecord
from hader.api import system
from hader.db import Base, _make_engine


DAY = date(2026, 8, 2)
STAMP = datetime(2026, 8, 2, 8)
SN = "COMMIT-EVENT"
UPLOAD = b"601\t2026-08-02 08:00:00\t0\t15\t0\t0\t0\n"


@pytest.fixture()
def isolated_sessions(tmp_path, monkeypatch):
    engine = _make_engine(f"sqlite:///{(tmp_path / 'events.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(events, "_listeners", [])
    with system._REPORTS_LOCK:
        previous_reports = system._REPORTS.copy()
        system._REPORTS.clear()
    with factory() as db:
        db.add(m.Employee(emp_code="601", first_name="Commit", last_name="Event"))
        db.add(m.Device(sn=SN, alias=SN, enabled=True))
        db.commit()
    try:
        yield factory
    finally:
        with system._REPORTS_LOCK:
            system._REPORTS.clear()
            system._REPORTS.update(previous_reports)
        engine.dispose()


def cached_transactions(factory):
    with factory() as db:
        return system._report_cached(db, "transactions", DAY, DAY, "en", "", "", "",
                                     offset=0, limit=200)


def punch_count(factory):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(m.Transaction))


def test_adms_commit_invalidates_report_built_before_commit(isolated_sessions, monkeypatch):
    factory = isolated_sessions
    ready_to_commit, allow_commit = Event(), Event()

    @contextmanager
    def paused_scope():
        with factory() as db:
            try:
                yield db
                ready_to_commit.set()
                if not allow_commit.wait(timeout=5):
                    raise RuntimeError("test did not release the pending ADMS commit")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    monkeypatch.setattr(server, "session_scope", paused_scope)
    with ThreadPoolExecutor(max_workers=1) as pool:
        upload = pool.submit(server.handle_upload, SN, "127.0.0.1", "ATTLOG", "1", UPLOAD)
        try:
            assert ready_to_commit.wait(timeout=5), "ADMS did not reach its commit boundary"
            # A real reader connection sees the old committed data while the
            # writer is paused, and stores that result in the report cache.
            assert cached_transactions(factory)["total"] == 0
        finally:
            allow_commit.set()
        assert upload.result(timeout=5) == "OK: 1"
    assert punch_count(factory) == 1
    assert cached_transactions(factory)["total"] == 1


def test_punch_event_observer_sees_committed_rows(isolated_sessions):
    factory = isolated_sessions
    observations = []
    events.listen(lambda kind, data: observations.append((kind, data, punch_count(factory))))
    with factory() as db:
        sync.save_punches(db, None, [AttRecord(pin="601", time=STAMP, state=0, verify=15)])
        assert observations == []
        db.commit()
    assert observations == [("punch", {"sn": "", "count": 1, "start": "2026-08-02", "end": "2026-08-02"}, 1)]


@pytest.mark.parametrize("finish", ["rollback", "close"])
def test_uncommitted_punch_does_not_publish_or_advance_generation(isolated_sessions, finish):
    factory = isolated_sessions
    seq, generation = events.current(), events.generation()
    db = factory()
    try:
        sync.save_punches(db, None, [AttRecord(pin="601", time=STAMP, state=0, verify=15)])
        getattr(db, finish)()
        # Reusing the session must not release events from its previous write.
        db.commit()
    finally:
        db.close()
    assert punch_count(factory) == 0
    assert events.current() == seq
    assert events.generation() == generation


@pytest.mark.parametrize("finish,expected_count", [("commit", 2), ("rollback", 1)])
def test_savepoint_does_not_publish_outer_transaction_early(isolated_sessions, finish, expected_count):
    factory = isolated_sessions
    seq = events.current()
    with factory() as db:
        sync.save_punches(db, None, [AttRecord(pin="601", time=STAMP)])
        savepoint = db.begin_nested()
        sync.save_punches(db, None, [AttRecord(pin="601", time=STAMP + timedelta(minutes=1))])
        getattr(savepoint, finish)()
        assert events.current() == seq
        db.commit()
    assert punch_count(factory) == expected_count
    assert len(events.since(seq)[1]) == expected_count
