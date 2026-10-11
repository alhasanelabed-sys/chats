"""A lost ADMS response must not turn concurrent retries into server errors."""
from concurrent.futures import ThreadPoolExecutor
import threading

from sqlalchemy import event, func, select
import pytest

from hader import intake, models as m
from hader.adms import sync
from hader.db import engine, session_scope


@pytest.mark.parametrize("known_employee", [True, False])
def test_simultaneous_retransmission_is_idempotent(client, monkeypatch, known_employee):
    if known_employee:
        assert client.post("/api/employees", json={"emp_code": "9901", "first_name": "Concurrent"}).status_code == 200
    assert client.post("/api/devices", json={"sn": "CONCURRENT"}).status_code == 200
    # Enrollment queries are a separate transport boundary; this test concerns
    # persisting ATTLOG data and acknowledging a retry after a lost response.
    monkeypatch.setattr(sync, "ask_for_missing", lambda *args: 0)
    barrier = threading.Barrier(2)

    def before_inbox_write(_conn, _cursor, statement, _params, _context, _many):
        # The durable inbox INSERT is now the first writer boundary, preceding
        # punch de-duplication. Synchronize before either SQLite writer owns the
        # transaction; a barrier after the punch read would deadlock the writer
        # with another request correctly waiting for that writer to commit.
        if statement.startswith('INSERT INTO intake_batch '):
            barrier.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", before_inbox_write)
    body = b"9901\t2026-08-02 08:00:00\t0\t15\t0\t0\n9901\t2026-08-02 16:00:00\t1\t15\t0\t0"

    def upload():
        return client.post("/iclock/cdata", params={"SN": "CONCURRENT", "table": "ATTLOG", "Stamp": "42"}, content=body)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(upload) for _ in range(2)]
            responses = [f.result(timeout=20) for f in futures]
        assert [(r.status_code, r.text) for r in responses] == [(200, "OK: 2"), (200, "OK: 2")]
    finally:
        event.remove(engine, "before_cursor_execute", before_inbox_write)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 2
        assert db.scalar(select(func.count()).select_from(m.Employee)) == 1
        assert db.scalar(select(func.count()).select_from(m.Transaction).where(m.Transaction.employee_id.is_(None))) == 0
        assert db.scalar(select(m.Device.att_stamp).where(m.Device.sn == "CONCURRENT")) == "42"
        batches = db.scalars(select(intake.IntakeBatch).order_by(intake.IntakeBatch.id)).all()
        assert len(batches) == 2
        assert sum(batch.accepted for batch in batches) == 2
        assert sum(batch.duplicates for batch in batches) == 2
        assert all(batch.received == 2 and batch.rejected == 0 and batch.raw == body for batch in batches)
