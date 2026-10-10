"""Dated policies preserve old attendance while exposing proposed impact."""
from datetime import datetime

import pytest


@pytest.fixture()
def worker(client):
    employee = client.post("/api/employees", json={"emp_code": "8641", "first_name": "Policy"}).json()
    shift = client.get("/api/shifts").json()["rows"][0]
    assert client.post("/api/schedules", json={"shift_id": shift["id"], "employee_ids": [employee["id"]],
                                               "start_date": "2026-01-01", "end_date": "2026-12-31"}).status_code == 200
    lines = "\n".join(f"8641\t2026-08-0{day} {stamp}\t0\t15\t0\t0\t0"
                      for day in (2, 3, 4) for stamp in ("08:20:00", "16:00:00"))
    assert client.post("/iclock/cdata?SN=POLICY1&table=ATTLOG", content=lines).status_code == 200
    return employee


def daily(client, worker, day):
    response = client.get("/api/attendance/daily", params={"start": day, "end": day,
                                                            "employee_ids": worker["id"]})
    assert response.status_code == 200, response.text
    return response.json()["rows"][0]


def revision(scope="rules", **extra):
    return {"scope": scope, "effective_from": "2026-08-03", "changes": {"att.late_full": False},
            "reason": "New rules from Monday", **extra}


def test_dated_rules_resolve_per_day_and_preserve_baseline(client, worker):
    assert daily(client, worker, "2026-08-02")["late"] == 20
    response = client.post("/api/attendance/policies", json=revision())
    assert response.status_code == 200, response.text
    assert daily(client, worker, "2026-08-02")["late"] == 20
    assert daily(client, worker, "2026-08-03")["late"] == 10
    rows = client.get("/api/attendance/daily", params={"start": "2026-08-02", "end": "2026-08-04",
                                                        "employee_ids": worker["id"]}).json()["rows"]
    assert [row["late"] for row in rows] == [20, 10, 10]
    history = client.get("/api/attendance/policies").json()
    assert history["total"] == 2
    assert any(row["baseline"] for row in history["rows"])
    assert response.json()["before"]["att.late_full"] is True
    assert response.json()["snapshot"]["att.late_full"] is False
    assert response.json()["actor"] == "admin"


def test_future_revision_does_not_change_current_or_earlier_results(client, worker):
    response = client.post("/api/attendance/policies", json=revision(effective_from="2090-01-01"))
    assert response.status_code == 200, response.text
    assert client.get("/api/settings").json()["att.late_full"] is True
    assert daily(client, worker, "2026-08-03")["late"] == 20


def test_preview_is_read_only_and_matches_saved_revision(client, worker):
    data = revision(start="2026-08-02", end="2026-08-04", employee_ids=[worker["id"]])
    response = client.post("/api/attendance/policies/preview", json=data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scanned_employee_days"] == 3
    assert result["changed_employee_days"] == 2
    assert result["before_totals"]["late"] == 60
    assert result["after_totals"]["late"] == 40
    assert client.get("/api/attendance/policies").json()["total"] == 0
    assert daily(client, worker, "2026-08-03")["late"] == 20
    assert client.post("/api/attendance/policies", json=revision()).status_code == 200
    assert daily(client, worker, "2026-08-03")["late"] == result["examples"][0]["after"]["late"]


def test_dated_timetable_and_ordinary_edits_keep_past_stable(client, worker):
    tt = client.get("/api/timetables").json()["rows"][0]
    response = client.post("/api/attendance/policies", json=revision(
        scope="timetable", target_id=tt["id"], changes={"late_grace": 25}))
    assert response.status_code == 200, response.text
    assert daily(client, worker, "2026-08-02")["late"] == 20
    assert daily(client, worker, "2026-08-03")["late"] == 0
    assert client.put(f"/api/timetables/{tt['id']}", json={"late_grace": 0}).status_code == 200
    assert daily(client, worker, "2026-08-03")["late"] == 0
    assert client.delete(f"/api/timetables/{tt['id']}").status_code == 409


def test_ordinary_settings_edit_after_dated_rules_keeps_historical_snapshot(client, worker):
    assert client.post("/api/attendance/policies", json=revision()).status_code == 200
    assert client.put("/api/settings", json={"att.late_full": True}).status_code == 200
    assert daily(client, worker, "2026-08-03")["late"] == 10
    assert client.get("/api/attendance/policies").json()["current"]["rules"]["snapshot"]["att.late_full"] is True


@pytest.mark.parametrize("change", [{"att.late_full": "false"}, {"att.no_out": "bad"},
                                     {"att.round_minutes": True}, {"att.weekend": [4, 4]},
                                     {"att.dup_punch_minutes": -1}, {"secret": "bad"}])
def test_strict_changes_rejected_without_history(client, change):
    response = client.post("/api/attendance/policies", json=revision(changes=change))
    assert response.status_code == 422, response.text
    assert client.get("/api/attendance/policies").json()["total"] == 0


def test_duplicate_rule_is_dated_for_night_shifts(client, worker):
    tt = client.post("/api/timetables", json={"alias": "Short night", "check_in": "23:59", "check_out": "00:05"}).json()
    assert client.post("/api/temp-schedules", json={"employee_ids": [worker["id"]], "start_date": "2026-08-02",
                                                   "timetable_ids": [tt["id"]]}).status_code == 200
    assert client.post("/iclock/cdata?SN=POLICY1&table=ATTLOG", content=
                       "8641\t2026-08-02 23:59:00\t0\t15\t0\t0\t0\n8641\t2026-08-03 00:05:00\t1\t15\t0\t0\t0").status_code == 200
    assert client.post("/api/attendance/policies", json=revision(changes={"att.dup_punch_minutes": 10})).status_code == 200
    # Its check-out crosses the effective date, but belongs to Sunday's policy.
    assert daily(client, worker, "2026-08-02")["clock_out"] == "2026-08-03 00:05"


def test_future_policy_rollover_ordinary_edits_merge_effective_snapshot(client, monkeypatch):
    from hader import policy_history as P
    assert client.post("/api/attendance/policies", json=revision(effective_from="2090-01-01")).status_code == 200
    tt = client.get("/api/timetables").json()["rows"][0]
    assert client.post("/api/attendance/policies", json=revision(scope="timetable", target_id=tt["id"],
        effective_from="2090-01-01", changes={"late_grace": 25})).status_code == 200
    # The stored legacy defaults have not advanced. On the effective date a
    # different-field edit must preserve the dated changes, not stale defaults.
    monkeypatch.setattr(P, "now", lambda: datetime(2090, 1, 2, 12))
    assert client.put("/api/settings", json={"att.ot_min_minutes": 60}).status_code == 200
    assert client.put(f"/api/timetables/{tt['id']}", json={"must_check_out": False}).status_code == 200
    current = client.get("/api/attendance/policies", params={"date": "2090-01-02"}).json()["current"]
    assert current["rules"]["snapshot"]["att.late_full"] is False
    assert current["rules"]["snapshot"]["att.ot_min_minutes"] == 60
    assert current["timetables"][0]["late_grace"] == 25
    assert current["timetables"][0]["must_check_out"] is False


def test_same_effective_date_revision_keeps_original_history(client, worker):
    first = client.post("/api/attendance/policies", json=revision()).json()
    second = client.post("/api/attendance/policies", json=revision(changes={"att.late_full": True}, reason="Revised after review"))
    assert second.status_code == 200, second.text
    assert second.json()["before"]["att.late_full"] is False
    assert second.json()["id"] != first["id"]
    assert daily(client, worker, "2026-08-03")["late"] == 20
    rows = client.get("/api/attendance/policies").json()["rows"]
    assert next(row for row in rows if row["id"] == first["id"])["snapshot"]["att.late_full"] is False


def test_invalid_timetable_change_rolls_back_history(client, worker):
    tt = client.get("/api/timetables").json()["rows"][0]
    assert client.post("/api/attendance/policies", json=revision(
        scope="timetable", target_id=tt["id"], changes={"late_grace": 25})).status_code == 200
    count = client.get("/api/attendance/policies").json()["total"]
    assert client.put(f"/api/timetables/{tt['id']}", json={"late_grace": -1}).status_code == 422
    assert client.get("/api/attendance/policies").json()["total"] == count
    assert daily(client, worker, "2026-08-03")["late"] == 0


def test_preview_rejects_excess_employee_days_before_engine_loading(client, monkeypatch):
    for index in range(14):
        assert client.post("/api/employees", json={"emp_code": str(8700 + index)}).status_code == 200
    from hader.api import attendance as A
    def unexpected_engine(*args, **kwargs):
        raise AssertionError("oversized preview must be rejected before loading raw punches")
    monkeypatch.setattr(A, "Engine", unexpected_engine)
    response = client.post("/api/attendance/policies/preview", json=revision(start="2026-01-01", end="2026-12-31"))
    assert response.status_code == 422, response.text
    assert "5000" in response.json()["detail"]


def test_read_only_role_can_explain_and_preview_but_cannot_revise(client, worker):
    role = client.post("/api/roles", json={"name": "Policy reader", "permissions": ["attendance.view"]}).json()
    assert client.post("/api/users", json={"username": "policy-reader", "password": "PolicyReaderPassword",
                                          "role_id": role["id"]}).status_code == 200
    from fastapi.testclient import TestClient
    from hader.app import app
    with TestClient(app) as reader:
        login = reader.post("/api/auth/login", json={"username": "policy-reader", "password": "PolicyReaderPassword"})
        assert login.status_code == 200
        assert reader.post("/api/auth/password", json={"old_password": "PolicyReaderPassword",
            "new_password": "PolicyReaderPassword2"}).status_code == 200
        assert reader.get(f"/api/attendance/explain/{worker['id']}?date=2026-08-02").status_code == 200
        assert reader.get("/api/attendance/policies").status_code == 200
        assert reader.post("/api/attendance/policies/preview", json=revision(start="2026-08-02", end="2026-08-04",
            employee_ids=[worker["id"]])).status_code == 200
        assert reader.post("/api/attendance/policies", json=revision()).status_code == 403
