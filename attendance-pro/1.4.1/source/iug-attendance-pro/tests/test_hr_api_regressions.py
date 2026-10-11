"""Public API regressions for attendance approvals and large staff schedules."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from hader.app import app


REQUEST_KINDS = ("leaves", "manual-logs", "overtimes")


def _post(client, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def hr_admin(client):
    """Use the real login flow, including first-use password enforcement."""
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    if me.json()["must_change_password"]:
        _post(client, "/api/auth/password", {
            "old_password": "admin", "new_password": "hr-regression-admin",
        })
    return client


@pytest.fixture()
def hr_employee(hr_admin):
    employee = _post(hr_admin, "/api/employees", {
        "emp_code": "710001", "first_name": "Review", "last_name": "Employee",
        "card_no": "987001", "hire_date": "2026-01-01",
    })
    leave_types = hr_admin.get("/api/leave-types")
    assert leave_types.status_code == 200, leave_types.text
    return employee, leave_types.json()["rows"][0]


@pytest.fixture()
def hr_editor(hr_admin):
    role = _post(hr_admin, "/api/roles", {
        "name": "Attendance editor without approval",
        "permissions": ["attendance.view", "attendance.edit"],
    })
    _post(hr_admin, "/api/users", {
        "username": "attendance-editor", "password": "hr-editor-secret",
        "role_id": role["id"],
    })
    editor = TestClient(app)
    login = _post(editor, "/api/auth/login", {
        "username": "attendance-editor", "password": "hr-editor-secret",
    })
    if login["user"]["must_change_password"]:
        _post(editor, "/api/auth/password", {
            "old_password": "hr-editor-secret", "new_password": "hr-editor-changed",
        })
    try:
        yield editor
    finally:
        editor.close()


def _request_body(kind, employee, leave_type):
    body = {"employee_id": employee["id"], "reason": "Original request"}
    if kind == "manual-logs":
        body.update(punch_time="2026-08-02 08:00", punch_state=0)
    else:
        body.update(start_time="2026-08-02 08:00", end_time="2026-08-02 10:00")
    if kind == "leaves":
        body["leave_type_id"] = leave_type["id"]
    return body


def _get_request(client, kind, request_id):
    response = client.get(f"/api/{kind}/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _assert_pending(row):
    assert row["status"] == "pending"
    assert row["approver"] == ""
    assert row["decided_at"] is None
    assert row["source"] == "admin"


@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_editor_creation_is_pending_and_ignores_forged_provenance(hr_editor, hr_employee, kind):
    employee, leave_type = hr_employee
    body = _request_body(kind, employee, leave_type) | {
        "approver": "forged-director", "decided_at": "2030-01-01 00:00",
        "source": "portal",
    }
    row = _post(hr_editor, f"/api/{kind}", body)
    _assert_pending(row)
    _assert_pending(_get_request(hr_editor, kind, row["id"]))


@pytest.mark.parametrize("kind", REQUEST_KINDS)
@pytest.mark.parametrize("status", ("approved", "rejected"))
def test_editor_cannot_create_a_decided_request(hr_editor, hr_employee, kind, status):
    employee, leave_type = hr_employee
    body = _request_body(kind, employee, leave_type) | {"status": status}
    response = hr_editor.post(f"/api/{kind}", json=body)
    assert response.status_code == 403, response.text
    listing = hr_editor.get(f"/api/{kind}")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0


@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_approver_default_remains_approved_with_server_decision(hr_admin, hr_employee, kind):
    employee, leave_type = hr_employee
    body = _request_body(kind, employee, leave_type) | {
        "approver": "forged-director", "decided_at": "2030-01-01 00:00",
        "source": "portal",
    }
    row = _post(hr_admin, f"/api/{kind}", body)
    assert row["status"] == "approved"
    assert row["approver"] == "admin"
    assert row["decided_at"] and not row["decided_at"].startswith("2030-")
    assert row["source"] == "admin"


@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_editor_revision_resubmits_approved_content_for_approval(hr_admin, hr_editor, hr_employee, kind):
    employee, leave_type = hr_employee
    row = _post(hr_admin, f"/api/{kind}", _request_body(kind, employee, leave_type))
    assert row["status"] == "approved"
    response = hr_editor.put(f"/api/{kind}/{row['id']}", json={
        "reason": "Revised request", "approver": "forged-director",
        "decided_at": "2030-01-01 00:00", "source": "portal",
    })
    assert response.status_code == 200, response.text
    _assert_pending(response.json())
    stored = _get_request(hr_admin, kind, row["id"])
    _assert_pending(stored)
    assert stored["reason"] == "Revised request"
    assert _post(hr_admin, f"/api/approvals/{kind}", {
        "ids": [row["id"]], "status": "approved",
    })["count"] == 1
    response = hr_editor.put(f"/api/{kind}/{row['id']}", json={
        "status": "approved", "reason": "Unauthorized revised request",
    })
    assert response.status_code == 403, response.text
    stored = _get_request(hr_admin, kind, row["id"])
    assert stored["status"] == "approved" and stored["reason"] == "Revised request"


@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_editor_cannot_use_the_approval_endpoint(hr_admin, hr_editor, hr_employee, kind):
    employee, leave_type = hr_employee
    row = _post(hr_editor, f"/api/{kind}", _request_body(kind, employee, leave_type))
    response = hr_editor.post(f"/api/approvals/{kind}", json={
        "ids": [row["id"]], "status": "approved",
    })
    assert response.status_code == 403, response.text
    _assert_pending(_get_request(hr_admin, kind, row["id"]))


@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_approval_counts_existing_rows_once_and_records_actual_actor(hr_admin, hr_editor, hr_employee, kind):
    employee, leave_type = hr_employee
    row = _post(hr_editor, f"/api/{kind}", _request_body(kind, employee, leave_type))
    result = _post(hr_admin, f"/api/approvals/{kind}", {
        "ids": [row["id"], row["id"], row["id"] + 1000000], "status": "approved",
        "approver": "forged-director", "decided_at": "2030-01-01 00:00",
    })
    assert result["count"] == 1
    stored = _get_request(hr_admin, kind, row["id"])
    assert stored["status"] == "approved" and stored["approver"] == "admin"
    assert stored["decided_at"] and not stored["decided_at"].startswith("2030-")
    assert _post(hr_admin, f"/api/approvals/{kind}", {
        "ids": [row["id"] + 1000000], "status": "rejected",
    })["count"] == 0


@pytest.mark.parametrize("kind,status", (("unknown", "approved"), ("leaves", "invalid")))
def test_approval_rejects_invalid_kind_or_status(hr_admin, kind, status):
    response = hr_admin.post(f"/api/approvals/{kind}", json={"ids": [], "status": status})
    assert response.status_code == 422, response.text


def _leave(employee, leave_type, start, end, status="approved"):
    return {
        "employee_id": employee["id"], "leave_type_id": leave_type["id"],
        "start_time": f"2026-08-02 {start}", "end_time": f"2026-08-02 {end}",
        "status": status,
    }


@pytest.mark.parametrize("status", ("pending", "approved"))
def test_active_leave_overlap_is_rejected_but_adjacent_leave_is_allowed(hr_admin, hr_employee, status):
    employee, leave_type = hr_employee
    _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "08:00", "12:00", status))
    response = hr_admin.post("/api/leaves", json=_leave(employee, leave_type, "10:00", "14:00", "pending"))
    assert response.status_code == 409, response.text
    adjacent = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "12:00", "14:00"))
    assert adjacent["status"] == "approved"
    other = _post(hr_admin, "/api/employees", {"emp_code": "710002"})
    assert _post(hr_admin, "/api/leaves", _leave(other, leave_type, "08:00", "12:00"))["employee_id"] == other["id"]


def test_leave_update_excludes_itself_and_rejects_overlap_without_saving(hr_admin, hr_employee):
    employee, leave_type = hr_employee
    first = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "08:00", "10:00"))
    second = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "10:00", "12:00"))
    response = hr_admin.put(f"/api/leaves/{first['id']}", json={"reason": "Same interval, corrected reason"})
    assert response.status_code == 200, response.text
    response = hr_admin.put(f"/api/leaves/{second['id']}", json={"start_time": "2026-08-02 09:00"})
    assert response.status_code == 409, response.text
    assert _get_request(hr_admin, "leaves", second["id"])["start_time"] == "2026-08-02 10:00:00"


def test_rejected_leave_does_not_block_new_leave_but_cannot_be_reapproved_over_it(hr_admin, hr_employee):
    employee, leave_type = hr_employee
    rejected = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "08:00", "12:00", "rejected"))
    approved = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "09:00", "10:00"))
    response = hr_admin.post("/api/approvals/leaves", json={"ids": [rejected["id"]], "status": "approved"})
    assert response.status_code == 409, response.text
    assert _get_request(hr_admin, "leaves", rejected["id"])["status"] == "rejected"
    assert _get_request(hr_admin, "leaves", approved["id"])["status"] == "approved"


def test_conflicting_batch_approval_is_atomic_including_nonconflicting_row(hr_admin, hr_employee):
    employee, leave_type = hr_employee
    unrelated = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "16:00", "18:00", "rejected"))
    first = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "08:00", "12:00", "rejected"))
    second = _post(hr_admin, "/api/leaves", _leave(employee, leave_type, "10:00", "14:00", "rejected"))
    ids = [unrelated["id"], first["id"], second["id"]]
    response = hr_admin.post("/api/approvals/leaves", json={"ids": ids, "status": "approved"})
    assert response.status_code == 409, response.text
    for request_id in ids:
        assert _get_request(hr_admin, "leaves", request_id)["status"] == "rejected"


def test_overriding_middle_of_rotating_schedule_preserves_following_rest_days(hr_admin, hr_employee):
    employee, _ = hr_employee
    timetable = hr_admin.get("/api/timetables").json()["rows"][0]
    rotating = _post(hr_admin, "/api/shifts", {
        "alias": "Regression 2 on 2 off", "cycle_unit": "day", "cycle": 4,
        "details": [{"day_index": i, "timetable_id": timetable["id"]} for i in (0, 1)],
    })
    rest = _post(hr_admin, "/api/shifts", {
        "alias": "Regression rest override", "cycle_unit": "day", "cycle": 1, "details": [],
    })
    _post(hr_admin, "/api/schedules", {
        "shift_id": rotating["id"], "employee_ids": [employee["id"]],
        "start_date": "2026-08-02", "end_date": "2026-08-09",
    })
    params = {"start": "2026-08-02", "end": "2026-08-09", "employee_ids": str(employee["id"])}
    before = hr_admin.get("/api/attendance/daily", params=params)
    assert before.status_code == 200, before.text
    assert [row["status"] for row in before.json()["rows"]] == [
        "absent", "absent", "off", "off", "absent", "absent", "off", "off",
    ]
    _post(hr_admin, "/api/schedules", {
        "shift_id": rest["id"], "employee_ids": [employee["id"]],
        "start_date": "2026-08-03", "end_date": "2026-08-03",
    })
    after = hr_admin.get("/api/attendance/daily", params=params)
    assert after.status_code == 200, after.text
    assert [row["status"] for row in after.json()["rows"]] == [
        "absent", "off", "off", "off", "absent", "absent", "off", "off",
    ]


@pytest.fixture()
def scheduled_staff(hr_admin):
    names = (("Alpha", "Harbor"), ("Bravo", "Island"), ("Charlie", "Jetty"), ("Delta", "Quay"))
    employees = [_post(hr_admin, "/api/employees", {
        "emp_code": str(71101 + i), "first_name": first, "last_name": last,
        "card_no": str(90101 + i),
    }) for i, (first, last) in enumerate(names)]
    shift = hr_admin.get("/api/shifts").json()["rows"][0]
    timetable = hr_admin.get("/api/timetables").json()["rows"][0]
    ids = [employee["id"] for employee in employees]
    _post(hr_admin, "/api/schedules", {
        "shift_id": shift["id"], "employee_ids": ids,
        "start_date": "2026-08-01", "end_date": "2026-08-31",
    })
    _post(hr_admin, "/api/temp-schedules", {
        "employee_ids": ids, "att_date": "2026-08-02", "timetable_ids": [timetable["id"]],
    })
    return employees


@pytest.mark.parametrize("path", ("/api/schedules", "/api/temp-schedules"))
def test_schedule_pagination_reports_full_total_and_normalizes_bounds(hr_admin, scheduled_staff, path):
    all_rows = hr_admin.get(path, params={"limit": 4}).json()
    assert all_rows["total"] == 4 and len(all_rows["rows"]) == 4
    response = hr_admin.get(path, params={"offset": 1, "limit": 2})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 4
    assert [row["id"] for row in page["rows"]] == [row["id"] for row in all_rows["rows"][1:3]]
    for limit in (0, -10):
        page = hr_admin.get(path, params={"offset": -4, "limit": limit}).json()
        assert page["total"] == 4 and len(page["rows"]) == 1
        assert page["rows"][0]["id"] == all_rows["rows"][0]["id"]
    page = hr_admin.get(path, params={"offset": 20, "limit": 2}).json()
    assert page["total"] == 4 and page["rows"] == []
    filtered = hr_admin.get(path, params={"employee_id": scheduled_staff[1]["id"], "limit": 1}).json()
    assert filtered["total"] == 1 and filtered["rows"][0]["employee_id"] == scheduled_staff[1]["id"]


@pytest.mark.parametrize("path", ("/api/schedules", "/api/temp-schedules"))
@pytest.mark.parametrize("query", ("71102", "Bravo", "Island", "90102"))
def test_schedule_search_finds_staff_by_code_first_last_or_card(hr_admin, scheduled_staff, path, query):
    response = hr_admin.get(path, params={"q": query, "limit": 1})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1 and len(page["rows"]) == 1
    assert page["rows"][0]["employee_id"] == scheduled_staff[1]["id"]


@pytest.mark.parametrize("path", ("/api/schedules", "/api/temp-schedules"))
def test_large_schedule_lists_count_every_row_and_cap_each_page_at_5000(hr_admin, path):
    # Bulk fixture creation keeps this boundary test economical. Requests still
    # exercise the real public endpoint, query, joins, serializer, and database.
    from hader import models as m
    from hader.db import session_scope

    shift_id = hr_admin.get("/api/shifts").json()["rows"][0]["id"]
    timetable_id = hr_admin.get("/api/timetables").json()["rows"][0]["id"]
    with session_scope() as db:
        employees = [m.Employee(emp_code=str(900000 + i)) for i in range(5002)]
        db.add_all(employees)
        db.flush()
        if path == "/api/schedules":
            db.add_all([m.Schedule(
                employee_id=employee.id, shift_id=shift_id,
                start_date=date(2026, 8, 1), end_date=date(2026, 8, 31),
            ) for employee in employees])
        else:
            db.add_all([m.TempSchedule(
                employee_id=employee.id, timetable_id=timetable_id, att_date=date(2026, 8, 2),
            ) for employee in employees])
    response = hr_admin.get(path, params={"limit": 10000})
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["total"] == 5002 and len(first["rows"]) == 5000
    response = hr_admin.get(path, params={"offset": 5000, "limit": 10000})
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["total"] == 5002 and len(second["rows"]) == 2
    assert not {row["id"] for row in first["rows"]} & {row["id"] for row in second["rows"]}
