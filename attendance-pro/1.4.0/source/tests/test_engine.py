"""Attendance calculation scenarios (timetable 08:00-16:00, grace 10/5, Sun-Thu)."""

import pytest

SUN, MON, TUE, WED, THU, FRI = (f"2026-08-0{d}" for d in (2, 3, 4, 5, 6, 7))


@pytest.fixture()
def emp(client):
    e = client.post("/api/employees", json={"emp_code": "501", "first_name": "Test", "hire_date": "2026-01-01"}).json()
    shift = client.get("/api/shifts").json()["rows"][0]
    r = client.post("/api/schedules", json={"shift_id": shift["id"], "employee_ids": [e["id"]],
                                            "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert r.status_code == 200, r.text
    return e


def punch(client, *times, sn="ENG1"):
    body = "\n".join(f"501\t{t}\t0\t15\t0\t0\t0" for t in times)
    r = client.post("/iclock/cdata", params={"SN": sn, "table": "ATTLOG"}, content=body.encode())
    assert r.status_code == 200


def day(client, date, emp_id):
    rows = client.get("/api/attendance/daily", params={"start": date, "end": date,
                                                       "employee_ids": str(emp_id)}).json()["rows"]
    assert len(rows) == 1
    return rows[0]


def test_on_time(client, emp):
    punch(client, f"{SUN} 07:55:00", f"{SUN} 16:05:00")
    d = day(client, SUN, emp["id"])
    assert d["status"] == "present" and d["worked"] == 480 and d["late"] == 0 and d["ot"] == 0
    assert d["clock_in"].endswith("07:55") and d["clock_out"].endswith("16:05")


def test_grace_then_late_and_early(client, emp):
    punch(client, f"{SUN} 08:09:00", f"{SUN} 16:00:00")
    assert day(client, SUN, emp["id"])["late"] == 0            # inside 10 min grace
    punch(client, f"{MON} 08:25:00", f"{MON} 15:30:00")
    d = day(client, MON, emp["id"])
    assert (d["late"], d["early"], d["status"]) == (25, 30, "late_early")
    assert d["worked"] == 480 - 25 - 30


def test_absent_and_missing_punch(client, emp):
    d = day(client, TUE, emp["id"])
    assert d["status"] == "absent" and d["absent"] == 480
    punch(client, f"{WED} 07:58:00")
    d = day(client, WED, emp["id"])
    assert d["status"] == "incomplete" and "missed_out" in d["exceptions"]


def test_missing_checkout_rule_absent(client, emp):
    client.put("/api/settings", json={"att.no_out": "absent"})
    punch(client, f"{WED} 07:58:00")
    assert day(client, WED, emp["id"])["status"] == "absent"


def test_day_off_work_is_overtime(client, emp):
    punch(client, f"{FRI} 09:00:00", f"{FRI} 13:00:00")
    d = day(client, FRI, emp["id"])
    assert d["status"] == "off" and d["worked"] == 240 and d["ot"] == 240


def test_auto_overtime_threshold(client, emp):
    punch(client, f"{SUN} 07:59:00", f"{SUN} 16:20:00")
    assert day(client, SUN, emp["id"])["ot"] == 0               # below 30 min minimum
    punch(client, f"{MON} 07:59:00", f"{MON} 17:10:00")
    assert day(client, MON, emp["id"])["ot"] == 70


def test_approved_overtime_mode(client, emp):
    client.put("/api/settings", json={"att.ot_mode": "approval"})
    punch(client, f"{SUN} 07:59:00", f"{SUN} 18:00:00")
    assert day(client, SUN, emp["id"])["ot"] == 0
    client.post("/api/overtimes", json={"employee_id": emp["id"], "start_time": f"{SUN} 16:00",
                                        "end_time": f"{SUN} 17:30", "status": "approved"})
    assert day(client, SUN, emp["id"])["ot"] == 90


def test_leave_and_holiday(client, emp):
    lt = client.get("/api/leave-types").json()["rows"][0]
    r = client.post("/api/leaves", json={"employee_id": emp["id"], "leave_type_id": lt["id"],
                                         "start_time": f"{TUE} 00:00", "end_time": f"{TUE} 23:59"})
    assert r.status_code == 200, r.text
    d = day(client, TUE, emp["id"])
    assert d["status"] == "leave" and d["absent"] == 0 and d["leave_codes"] == [lt["code"]]
    client.post("/api/holidays", json={"alias": "National day", "start_date": WED, "days": 2})
    d = day(client, WED, emp["id"])
    assert d["status"] == "holiday" and d["absent"] == 0 and d["holiday"] == "National day"
    assert day(client, THU, emp["id"])["status"] == "holiday"


def test_partial_leave_excuses_lateness(client, emp):
    lt = client.get("/api/leave-types").json()["rows"][0]
    client.post("/api/leaves", json={"employee_id": emp["id"], "leave_type_id": lt["id"],
                                     "start_time": f"{SUN} 08:00", "end_time": f"{SUN} 10:00"})
    punch(client, f"{SUN} 10:00:00", f"{SUN} 16:00:00")
    d = day(client, SUN, emp["id"])
    assert d["late"] == 0 and d["leave"] == 120 and d["status"] == "present"


def test_pending_leave_does_not_count(client, emp):
    lt = client.get("/api/leave-types").json()["rows"][0]
    r = client.post("/api/leaves", json={"employee_id": emp["id"], "leave_type_id": lt["id"], "status": "pending",
                                         "start_time": f"{TUE} 00:00", "end_time": f"{TUE} 23:59"}).json()
    assert day(client, TUE, emp["id"])["status"] == "absent"
    client.post("/api/approvals/leaves", json={"ids": [r["id"]], "status": "approved"})
    assert day(client, TUE, emp["id"])["status"] == "leave"


def test_manual_punch(client, emp):
    punch(client, f"{SUN} 07:50:00")
    client.post("/api/manual-logs", json={"employee_id": emp["id"], "punch_time": f"{SUN} 16:02",
                                          "punch_state": 1, "reason": "forgot"})
    d = day(client, SUN, emp["id"])
    assert d["status"] == "present" and d["worked"] == 480


def test_overnight_and_break_timetables(client, emp):
    night = client.post("/api/timetables", json={"alias": "Night", "check_in": "22:00", "check_out": "06:00",
                                                 "late_grace": 0}).json()
    lunch = client.post("/api/timetables", json={"alias": "Lunch", "check_in": "08:00", "check_out": "17:00",
                                                 "break_start": "12:00", "break_end": "13:00"}).json()
    client.post("/api/temp-schedules", json={"employee_ids": [emp["id"]], "start_date": SUN,
                                             "timetable_ids": [night["id"]]})
    client.post("/api/temp-schedules", json={"employee_ids": [emp["id"]], "start_date": MON,
                                             "timetable_ids": [lunch["id"]]})
    punch(client, f"{SUN} 21:55:00", f"{MON} 06:03:00", f"{MON} 07:58:00", f"{MON} 17:01:00")
    d = day(client, SUN, emp["id"])
    assert d["status"] == "present" and d["worked"] == 480 and d["clock_out"] == f"{MON} 06:03"
    d = day(client, MON, emp["id"])
    assert d["required"] == 480 and d["worked"] == 480 and d["status"] == "present"
    assert d["clock_in"].endswith("07:58")


def test_flexible_timetable(client, emp):
    flex = client.post("/api/timetables", json={"alias": "Flex", "kind": "flexible", "check_in": "00:00",
                                                "check_out": "23:59", "work_minutes": 480}).json()
    client.post("/api/temp-schedules", json={"employee_ids": [emp["id"]], "start_date": SUN, "end_date": MON,
                                             "timetable_ids": [flex["id"]]})
    punch(client, f"{SUN} 10:00:00", f"{SUN} 18:45:00", f"{MON} 09:00:00", f"{MON} 16:00:00")
    d = day(client, SUN, emp["id"])
    assert d["worked"] == 525 and d["ot"] == 45 and d["late"] == 0
    d = day(client, MON, emp["id"])
    assert d["early"] == 60 and d["status"] == "early"


def test_temp_schedule_day_off(client, emp):
    client.post("/api/temp-schedules", json={"employee_ids": [emp["id"]], "start_date": TUE, "timetable_ids": []})
    assert day(client, TUE, emp["id"])["status"] == "off"


def test_duplicate_punches_collapse(client, emp):
    punch(client, f"{SUN} 07:55:00", f"{SUN} 07:55:30", f"{SUN} 16:01:00")
    assert day(client, SUN, emp["id"])["punches"] == ["07:55", "16:01"]


def test_rotating_day_shift(client, emp):
    tt = client.get("/api/timetables").json()["rows"][0]
    # 2 days on, 2 days off
    s = client.post("/api/shifts", json={"alias": "2on2off", "cycle_unit": "day", "cycle": 4,
                                         "details": [{"day_index": 0, "timetable_id": tt["id"]},
                                                     {"day_index": 1, "timetable_id": tt["id"]}]}).json()
    client.post("/api/schedules", json={"shift_id": s["id"], "employee_ids": [emp["id"]],
                                        "start_date": SUN, "end_date": "2026-08-31"})
    statuses = [day(client, d, emp["id"])["status"] for d in (SUN, MON, TUE, WED, THU)]
    assert statuses == ["absent", "absent", "off", "off", "absent"]


def test_not_hired_yet_has_no_rows(client):
    e = client.post("/api/employees", json={"emp_code": "502", "hire_date": "2026-08-05"}).json()
    rows = client.get("/api/attendance/daily", params={"start": SUN, "end": THU,
                                                       "employee_ids": str(e["id"])}).json()["rows"]
    assert [r["date"] for r in rows] == [WED, THU]


def test_reports_and_exports(client, emp):
    punch(client, f"{SUN} 08:30:00", f"{SUN} 16:00:00", f"{MON} 07:50:00")
    params = {"start": SUN, "end": FRI, "employee_ids": str(emp["id"])}
    rep = client.get("/api/reports/late", params=params).json()
    assert len(rep["rows"]) == 1 and rep["rows"][0]["late_hm"] == "0:30"
    s = client.get("/api/reports/summary", params=params).json()["rows"][0]
    assert s["late_count"] == 1 and s["absent_days"] == 3 and s["missed_punch"] == 1
    m = client.get("/api/reports/monthly_status", params=params).json()
    row = m["rows"][0]
    assert row[SUN] == "L" and row[MON] == "!" and row[TUE] == "A" and row[FRI] == "-"
    for key in ("transactions", "first_last", "time_card", "daily", "early", "absent", "overtime",
                "exception", "leave", "department"):
        r = client.get(f"/api/reports/{key}", params=params)
        assert r.status_code == 200, key
    x = client.get("/api/reports/daily", params=params | {"fmt": "xlsx"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    c = client.get("/api/reports/daily", params=params | {"fmt": "csv", "lang": "en"})
    assert c.content.startswith("﻿".encode()) and b"Clock in" in c.content
