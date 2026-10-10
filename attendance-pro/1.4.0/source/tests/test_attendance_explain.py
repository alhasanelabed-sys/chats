"""Explanation evidence must agree with the same attendance calculation."""
import pytest


@pytest.fixture()
def person(client):
    employee = client.post("/api/employees", json={"emp_code": "8642", "first_name": "Evidence"}).json()
    shift = client.get("/api/shifts").json()["rows"][0]
    assert client.post("/api/schedules", json={"shift_id": shift["id"], "employee_ids": [employee["id"]],
                                               "start_date": "2026-01-01", "end_date": "2026-12-31"}).status_code == 200
    return employee


def logs(client, *timestamps):
    body = "\n".join(f"8642\t2026-08-02 {stamp}\t0\t15\t0\t0\t0" for stamp in timestamps)
    assert client.post("/iclock/cdata?SN=EXPLAIN1&table=ATTLOG", content=body).status_code == 200


def explain(client, person, date="2026-08-02"):
    response = client.get(f"/api/attendance/explain/{person['id']}", params={"date": date})
    assert response.status_code == 200, response.text
    return response.json()


def test_explain_selected_duplicate_additional_and_result_consistency(client, person):
    logs(client, "08:20:00", "08:20:30", "10:00:00", "16:00:00")
    result = explain(client, person)
    daily = client.get("/api/attendance/daily", params={"start": "2026-08-02", "end": "2026-08-02",
                                                        "employee_ids": person["id"]}).json()["rows"][0]
    assert result["result"] == daily
    reasons = {punch["time"][-8:]: punch["reason"] for punch in result["punches"]}
    assert reasons == {"08:20:00": "selected_check_in", "08:20:30": "duplicate_window",
                       "10:00:00": "additional_punch", "16:00:00": "selected_check_out"}
    assert result["schedules"]["source"] == "employee"
    assert result["result"]["late"] == 20
    assert result["rules"]["values"]["att.dup_punch_minutes"] == 1
    assert "photo" not in str(result["punches"][0])
    assert any(warning["code"] == "state_is_metadata" for warning in result["warnings"])


def test_manual_approval_and_nonattendance_sources_are_explained(client, person):
    logs(client, "08:00:00")
    manual = client.post("/api/manual-logs", json={"employee_id": person["id"], "punch_time": "2026-08-02 16:00",
                                                 "punch_state": 1, "reason": "Forgot terminal", "status": "pending"}).json()
    from hader.db import session_scope
    from hader import models as m
    with session_scope() as db:
        db.add(m.Device(sn="ACCESS1", alias="Access gate", is_attendance=False))
        db.add(m.Transaction(employee_id=person["id"], emp_code=person["emp_code"], device_sn="ACCESS1",
                             punch_time=__import__("datetime").datetime(2026, 8, 2, 15)))
    first = explain(client, person)
    assert {p["reason"] for p in first["punches"]} >= {"manual_not_approved", "non_attendance_device"}
    assert first["result"]["status"] == "incomplete"
    assert client.post("/api/approvals/manual-logs", json={"ids": [manual["id"]], "status": "approved"}).status_code == 200
    final = explain(client, person)
    correction = next(p for p in final["punches"] if p["kind"] == "manual")
    assert correction["selected"] and correction["reason"] == "selected_check_out"
    assert correction["approver"] == "admin" and correction["decided_at"]
    assert final["result"]["status"] == "present"


def test_explain_uses_dated_rule_and_overnight_evidence(client, person):
    night = client.post("/api/timetables", json={"alias": "Evidence night", "check_in": "22:00", "check_out": "06:00"}).json()
    assert client.post("/api/temp-schedules", json={"employee_ids": [person["id"]], "start_date": "2026-08-02",
                                                   "timetable_ids": [night["id"]]}).status_code == 200
    body = "8642\t2026-08-02 22:10:00\t0\t15\t0\t0\t0\n8642\t2026-08-03 06:00:00\t1\t15\t0\t0\t0"
    assert client.post("/iclock/cdata?SN=EXPLAIN1&table=ATTLOG", content=body).status_code == 200
    revision = client.post("/api/attendance/policies", json={"scope": "rules", "effective_from": "2026-08-02",
                                                            "changes": {"att.round_minutes": 15}, "reason": "Round work minutes"})
    assert revision.status_code == 200, revision.text
    result = explain(client, person)
    assert result["rules"]["version"] == revision.json()["id"]
    assert result["schedules"]["source"] == "temporary"
    assert any(p["time"] == "2026-08-03 06:00:00" and p["selected"] for p in result["punches"])
    assert result["result"]["worked"] == 465


def test_explain_requires_permission_and_valid_employee_date(client, person):
    assert client.get("/api/attendance/explain/999999", params={"date": "2026-08-02"}).status_code == 404
    assert client.get(f"/api/attendance/explain/{person['id']}", params={"date": "bad"}).status_code == 422
    client.post("/api/auth/logout")
    assert client.get(f"/api/attendance/explain/{person['id']}").status_code == 401
