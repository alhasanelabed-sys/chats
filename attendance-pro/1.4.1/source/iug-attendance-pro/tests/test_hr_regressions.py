"""Attendance invariants using real persisted timetables and raw punches."""
from datetime import date, datetime, time

import pytest

from hader import models as m, store
from hader.db import session_scope
from hader.engine import Engine, summarize

SUN = date(2026, 8, 2)
MON = date(2026, 8, 3)


@pytest.fixture()
def attendance_db(client):
    with session_scope() as db:
        emp = m.Employee(emp_code="601", first_name="Attendance", hire_date=date(2026, 1, 1))
        db.add(emp)
        db.flush()
        yield db, emp


def timetable(db, day, emp, **kwargs):
    tt = m.TimeTable(alias=f"Regression {day} {len(db.new)} {kwargs.get('check_in', time(8))}",
                     check_in=time(8), check_out=time(16))
    for key, value in kwargs.items():
        setattr(tt, key, value)
    db.add(tt)
    db.flush()
    db.add(m.TempSchedule(employee_id=emp.id, att_date=day, timetable_id=tt.id))
    db.flush()
    return tt


def punch(db, emp, *timestamps):
    db.add_all(m.Transaction(employee_id=emp.id, emp_code=emp.emp_code,
                            punch_time=datetime.fromisoformat(ts), device_sn="") for ts in timestamps)
    db.flush()


def leave(db, emp, start, end):
    lt = db.query(m.LeaveType).first()
    db.add(m.Leave(employee_id=emp.id, leave_type_id=lt.id, status="approved",
                   start_time=datetime.fromisoformat(start), end_time=datetime.fromisoformat(end)))
    db.flush()


def overtime(db, emp, start, end):
    db.add(m.Overtime(employee_id=emp.id, status="approved", start_time=datetime.fromisoformat(start),
                      end_time=datetime.fromisoformat(end)))
    db.flush()


def result(db, emp, day=SUN):
    return Engine(db, day, day, employee_ids=[emp.id]).run()[0]


def test_missing_one_segment_remains_visible_as_partial_absence(attendance_db):
    db, emp = attendance_db
    timetable(db, SUN, emp, check_in=time(8), check_out=time(12), workday=.5)
    timetable(db, SUN, emp, check_in=time(14), check_out=time(18), workday=.5)
    punch(db, emp, "2026-08-02 14:00", "2026-08-02 18:00")
    r = result(db, emp)
    assert (r.required, r.worked, r.absent, r.status) == (480, 240, 240, "partial_absent")
    summary = summarize([r])[0]
    assert summary["present_days"] == .5
    assert summary["partial_absent_days"] == 1
    assert summary["absent_minutes"] == 240


def test_night_checkout_is_not_day_off_entry_even_when_requested_alone(attendance_db):
    db, emp = attendance_db
    timetable(db, SUN, emp, check_in=time(22), check_out=time(6), out_above=240)
    db.add(m.TempSchedule(employee_id=emp.id, att_date=MON, timetable_id=None))
    punch(db, emp, "2026-08-02 22:00", "2026-08-03 06:00", "2026-08-03 11:00", "2026-08-03 15:00")
    night = result(db, emp)
    off = result(db, emp, MON)
    assert (night.worked, night.ot) == (480, 0)
    assert (off.worked, off.ot) == (240, 240)
    assert off.clock_in == datetime(2026, 8, 3, 11)
    assert off.punches == [datetime(2026, 8, 3, 11), datetime(2026, 8, 3, 15)]


def test_leave_includes_break_only_once_and_preserves_real_lateness(attendance_db):
    db, emp = attendance_db
    timetable(db, SUN, emp, check_out=time(17), break_start=time(12), break_end=time(13), in_above=360)
    leave(db, emp, "2026-08-02 08:00", "2026-08-02 13:00")
    punch(db, emp, "2026-08-02 13:30", "2026-08-02 17:00")
    r = result(db, emp)
    assert (r.required, r.leave, r.worked, r.late) == (480, 240, 210, 30)


def test_historic_overlapping_leaves_are_counted_as_union(attendance_db):
    db, emp = attendance_db
    timetable(db, SUN, emp)
    leave(db, emp, "2026-08-02 08:00", "2026-08-02 11:00")
    leave(db, emp, "2026-08-02 10:00", "2026-08-02 12:00")
    r = result(db, emp)
    assert (r.leave, r.absent, r.status) == (240, 240, "absent")


@pytest.mark.parametrize("approved_start,approved_end", [
    ("2026-08-02 16:00", "2026-08-02 18:00"),
    ("2026-08-02 08:00", "2026-08-02 10:00"),
    ("2026-08-02 20:00", "2026-08-02 22:00"),
])
def test_overtime_requires_actual_presence_outside_regular_work(attendance_db, approved_start, approved_end):
    db, emp = attendance_db
    store.set_(db, "att.ot_mode", "approval")
    timetable(db, SUN, emp)
    timetable(db, MON, emp)
    punch(db, emp, "2026-08-02 08:00", "2026-08-02 16:00", "2026-08-03 08:00", "2026-08-03 16:00")
    overtime(db, emp, approved_start, approved_end)
    assert result(db, emp).ot == 0
    assert result(db, emp, MON).ot == 0


def test_overlapping_approved_overtime_uses_union_and_actual_checkout(attendance_db):
    db, emp = attendance_db
    store.set_(db, "att.ot_mode", "approval")
    timetable(db, SUN, emp)
    punch(db, emp, "2026-08-02 08:00", "2026-08-02 17:30")
    overtime(db, emp, "2026-08-02 16:00", "2026-08-02 17:00")
    overtime(db, emp, "2026-08-02 16:30", "2026-08-02 18:00")
    assert result(db, emp).ot == 90


def test_day_off_overtime_obeys_approval_mode(attendance_db):
    db, emp = attendance_db
    store.set_(db, "att.ot_mode", "approval")
    db.add(m.TempSchedule(employee_id=emp.id, att_date=SUN, timetable_id=None))
    punch(db, emp, "2026-08-02 09:00", "2026-08-02 13:00")
    overtime(db, emp, "2026-08-02 10:00", "2026-08-02 11:00")
    assert result(db, emp).ot == 60


def test_flexible_overtime_only_approves_work_above_required_minutes(attendance_db):
    db, emp = attendance_db
    store.set_(db, "att.ot_mode", "approval")
    timetable(db, SUN, emp, kind="flexible", check_in=time(0), check_out=time(23, 59), work_minutes=480)
    punch(db, emp, "2026-08-02 09:00", "2026-08-02 18:00")
    overtime(db, emp, "2026-08-02 15:00", "2026-08-02 18:30")
    assert result(db, emp).ot == 60


def test_both_overtime_modes_unions_disjoint_approved_and_auto_time(attendance_db):
    db, emp = attendance_db
    store.set_(db, "att.ot_mode", "both")
    timetable(db, SUN, emp)
    punch(db, emp, "2026-08-02 07:00", "2026-08-02 17:00")
    overtime(db, emp, "2026-08-02 07:00", "2026-08-02 08:00")
    assert result(db, emp).ot == 120


def test_generator_and_count_respect_employment_dates_and_paging(attendance_db):
    db, emp = attendance_db
    emp.hire_date = date(2026, 8, 3)
    emp.status, emp.resign_date = "resigned", date(2026, 8, 5)
    db.flush()
    engine = Engine(db, SUN, date(2026, 8, 7), employee_ids=[emp.id])
    assert engine.row_count() == 3
    assert [r.att_date for r in engine.iter_rows(offset=1, limit=1)] == [date(2026, 8, 4)]
    assert [r.as_dict() for r in engine.iter_rows()] == [r.as_dict() for r in engine.run()]
