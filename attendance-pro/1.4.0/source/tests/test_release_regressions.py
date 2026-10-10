"""Public regressions found during release verification."""
from dataclasses import replace
from datetime import date
import sqlite3

import pytest
from sqlalchemy import select

from hader import backup as B, models as m, runtime
from hader.api import system
from hader.db import session_scope
from hader.engine import Engine


@pytest.mark.parametrize("known,expected", [(False, 403), (True, 200)])
def test_device_command_acknowledgement_is_plain_text(client, known, expected):
    if known:
        assert client.post("/api/devices", json={"sn": "ACK"}).status_code == 200
    r = client.post("/iclock/devicecmd", params={"SN": "ACK"}, content=b"ID=999&Return=0&CMD=INFO")
    assert r.status_code == expected
    assert r.text == ("OK" if known else "UNKNOWN DEVICE")


@pytest.mark.parametrize("path", ["cdata", "getrequest", "rtdata"])
def test_device_source_acl_returns_protocol_error(client, path):
    assert client.post("/api/devices", json={
        "sn": "ACL", "adms_allowed_ips": ["192.0.2.1"],
    }).status_code == 200
    r = client.get(f"/iclock/{path}", params={"SN": "ACL"})
    assert r.status_code == 403


def test_backup_restore_endpoint_schedules_and_audits(client, tmp_path, monkeypatch):
    # Stage a real snapshot in a separate data directory; never replace the
    # database serving this TestClient or leave a restart marker between tests.
    s = replace(B.settings, data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'hader.db'}")
    with sqlite3.connect(B._db_path()) as source, sqlite3.connect(tmp_path / "hader.db") as target:
        source.backup(target)
    monkeypatch.setattr(B, "settings", s)
    monkeypatch.setattr(system, "settings", s)
    snapshot = B.create_snapshot()
    r = client.post(f"/api/backups/{snapshot.name}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["scheduled"] and r.json()["restarting"]
    assert (tmp_path / ".pending_restore.json").is_file()
    assert runtime.RESTART.wait(2), "the scheduled restart must be signalled"
    with session_scope() as db:
        row = db.scalar(select(m.AuditLog).where(m.AuditLog.action == "restore.scheduled"))
        assert row is not None and row.username == "admin" and row.detail == snapshot.name


def test_explicit_empty_employee_filter_never_expands_to_all(client):
    assert client.post("/api/employees", json={"emp_code": "901"}).status_code == 200
    with session_scope() as db:
        engine = Engine(db, date(2026, 8, 2), date(2026, 8, 2), employee_ids=[])
        assert engine.run() == [] and engine.row_count() == 0


def test_historical_dashboard_trend_refreshes_after_late_upload(client, monkeypatch):
    monkeypatch.setattr(system, "_TREND_CACHE", {})
    day = date(2026, 8, 2)
    with session_scope() as db:
        emp = m.Employee(emp_code="909", first_name="Late upload")
        db.add(emp)
        db.flush()
        db.add(m.TempSchedule(employee_id=emp.id, att_date=day,
                              timetable_id=db.query(m.TimeTable).first().id))
    with session_scope() as db:
        assert system._past_trend(db, day, day)[0]["present"] == 0
    assert client.post("/api/devices", json={"sn": "TREND"}).status_code == 200
    upload = client.post("/iclock/cdata", params={"SN": "TREND", "table": "ATTLOG"},
                         content=b"909\t2026-08-02 08:00:00\t0\t15\t0\t0\t0\n")
    assert upload.status_code == 200
    with session_scope() as db:
        assert system._past_trend(db, day, day)[0]["present"] == 1


def test_api_changes_mark_dashboard_snapshot_dirty(client):
    # API mutations commit before the response; the next dashboard request
    # should schedule a rebuild instead of waiting for the minute timer.
    system._SNAP_DIRTY.clear()
    assert client.post("/api/employees", json={"emp_code": "910"}).status_code == 200
    assert system._SNAP_DIRTY.is_set()


@pytest.mark.parametrize("key", ["summary", "monthly_status"])
@pytest.mark.parametrize("filter_query", [{"offset": 1}, {"q": "does-not-exist"}])
def test_empty_report_page_does_not_leak_all_employees(client, key, filter_query):
    assert client.post("/api/employees", json={"emp_code": "901"}).status_code == 200
    r = client.get(f"/api/reports/{key}", params={
        "start": "2026-08-02", "end": "2026-08-02", "limit": 1, **filter_query,
    })
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [] and r.json()["has_more"] is False


@pytest.mark.parametrize("path", ["daily", "summary"])
@pytest.mark.parametrize("start,end", [("2026-08-03", "2026-08-02"), ("2020-01-01", "2026-08-02")])
def test_attendance_rejects_invalid_or_unbounded_range(client, path, start, end):
    r = client.get(f"/api/attendance/{path}", params={"start": start, "end": end})
    assert r.status_code == 422


def test_report_employee_total_excludes_dates_outside_employment(client):
    for code, extra in [("901", {}), ("902", {"hire_date": "2027-01-01"}),
                        ("903", {"status": "resigned", "resign_date": "2026-01-01"})]:
        r = client.post("/api/employees", json={"emp_code": code, **extra})
        assert r.status_code == 200, r.text
        if extra.get("status") == "resigned":
            r = client.post("/api/employees/batch", json={"ids": [r.json()["id"]],
                "action": "resign", "resign_date": extra["resign_date"]})
            assert r.status_code == 200, r.text
    for key in ("summary", "monthly_status"):
        r = client.get(f"/api/reports/{key}", params={"start": "2026-08-02", "end": "2026-08-02", "limit": 1})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1 and r.json()["rows"][0]["emp_code"] == "901"
        assert r.json()["has_more"] is False


@pytest.mark.parametrize("path", ["/api/attendance/daily", "/api/reports/daily"])
def test_unfiltered_page_calculates_only_requested_days(client, monkeypatch, path):
    from hader.calculation_cache import clear
    clear()  # This regression measures cold calculation scope, not a valid warm-cache hit.
    for code in ("901", "902", "903"):
        assert client.post("/api/employees", json={"emp_code": code}).status_code == 200
    calculated = []
    original = Engine.day

    def calculate(self, employee, day, today):
        calculated.append((employee.emp_code, day))
        return original(self, employee, day, today)

    monkeypatch.setattr(Engine, "day", calculate)
    r = client.get(path, params={"start": "2026-08-01", "end": "2026-08-10", "offset": 28, "limit": 2})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 30 and r.json()["has_more"] is False
    assert [(row["emp_code"], row["date"]) for row in r.json()["rows"]] == [
        ("903", "2026-08-09"), ("903", "2026-08-10")]
    assert calculated == [("903", date(2026, 8, 9)), ("903", date(2026, 8, 10))]


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_device_write_opt_in_requires_an_actual_boolean(client, value):
    assert client.put("/api/settings", json={"tcp.write_back": False}).status_code == 200
    r = client.put("/api/settings", json={"tcp.write_back": value})
    assert r.status_code == 422
    assert client.get("/api/settings").json()["tcp.write_back"] is False
