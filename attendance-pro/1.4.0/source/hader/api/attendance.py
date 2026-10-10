"""Attendance module: timetables, shifts, schedules, holidays, leave,
manual punches, overtime and the calculated attendance view."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import delete, insert, or_, select
from sqlalchemy.orm import Session

from ..db import get_db, now
from .. import models as m
from ..engine import Engine, employee_day_page, iter_attendance, summarize
from ..security import user_permissions
from .. import policy_history as policies
from .crud import crud_router
from .deps import audit, ids_param, page, parse_date, require, ser

router = APIRouter(prefix="/api")


def _employee_search(q: str):
    pattern = f"%{q.strip()}%"
    return or_(m.Employee.emp_code.ilike(pattern), m.Employee.first_name.ilike(pattern),
               m.Employee.last_name.ilike(pattern), m.Employee.card_no.ilike(pattern),
               (m.Employee.first_name + " " + m.Employee.last_name).ilike(pattern))


def _tt_before(db, obj, data, is_new):
    if obj.kind not in ("normal", "flexible"):
        raise HTTPException(422, "kind must be normal or flexible")
    for f in ("in_ahead", "in_above", "out_ahead", "out_above", "late_grace", "early_grace"):
        if (getattr(obj, f) or 0) < 0:
            raise HTTPException(422, f"{f} cannot be negative")


def _tt_history_guard(db, request, user, obj, changes, action):
    if action == "delete" and db.scalar(select(policies.AttendancePolicy.id).where(
            policies.AttendancePolicy.scope == "timetable", policies.AttendancePolicy.target_id == obj.id).limit(1)):
        raise HTTPException(409, "فترة الدوام لها سجل مؤرخ؛ احتفظ بها لحماية التقارير السابقة / "
                                 "This timetable has dated history; retain it to protect earlier reports")
    if action == "update":
        db.info["policy_actor"] = user.username
        policies.capture_timetable_change(db, obj, changes)


def _tt_current(db, obj):
    revision = policies.PolicyTimeline(db).at("timetable", obj.id, now().date())
    return {"id": obj.id, **revision["snapshot"]} if revision else ser(obj)


router.include_router(crud_router(m.TimeTable, "/timetables", "attendance.view", "attendance.edit",
                                  search=("alias",), order=m.TimeTable.alias, before_save=_tt_before,
                                  mutation_guard=_tt_history_guard, to_dict=_tt_current))
router.include_router(crud_router(m.Holiday, "/holidays", "attendance.view", "attendance.edit",
                                  search=("alias",), order=m.Holiday.start_date.desc()))
router.include_router(crud_router(m.LeaveType, "/leave-types", "attendance.view", "attendance.edit",
                                  search=("code", "name"), order=m.LeaveType.code))


# --------------------------------------------------------------------------
# Shifts
# --------------------------------------------------------------------------

def shift_dict(db, s: m.Shift) -> dict:
    d = ser(s)
    d["details"] = [{"day_index": x.day_index, "timetable_id": x.timetable_id} for x in s.details]
    names = {t.id: t.alias for t in db.scalars(select(m.TimeTable)).all()}
    d["summary"] = ", ".join(sorted({names.get(x.timetable_id, "?") for x in s.details}))
    return d


def _save_shift(db: Session, s: m.Shift, data: dict) -> None:
    s.alias = (data.get("alias") or s.alias or "").strip()
    if not s.alias:
        raise HTTPException(422, "name required")
    s.cycle_unit = data.get("cycle_unit", s.cycle_unit or "week")
    if s.cycle_unit not in ("day", "week", "month"):
        raise HTTPException(422, "cycle unit must be day, week or month")
    s.cycle = max(1, int(data.get("cycle", s.cycle or 1)))
    if "details" in data:
        span = s.cycle * {"day": 1, "week": 7, "month": 31}[s.cycle_unit]
        s.details = [m.ShiftDetail(day_index=int(x["day_index"]), timetable_id=int(x["timetable_id"]))
                     for x in data["details"] if 0 <= int(x["day_index"]) < span]


@router.get("/shifts")
def list_shifts(db: Session = Depends(get_db), _=Depends(require("attendance.view"))):
    rows = db.scalars(select(m.Shift).order_by(m.Shift.alias)).all()
    return {"total": len(rows), "rows": [shift_dict(db, s) for s in rows]}


@router.post("/shifts")
def create_shift(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                 _=Depends(require("attendance.edit"))):
    s = m.Shift()
    _save_shift(db, s, data)
    db.add(s)
    db.flush()
    audit(db, request, "create", "shift", s.alias)
    db.commit()
    return shift_dict(db, s)


@router.put("/shifts/{shift_id}")
def update_shift(shift_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                 _=Depends(require("attendance.edit"))):
    s = db.get(m.Shift, shift_id)
    if not s:
        raise HTTPException(404, "not found")
    _save_shift(db, s, data)
    audit(db, request, "update", "shift", s.alias)
    db.commit()
    return shift_dict(db, s)


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int, request: Request, db: Session = Depends(get_db),
                 _=Depends(require("attendance.edit"))):
    s = db.get(m.Shift, shift_id)
    if not s:
        raise HTTPException(404, "not found")
    db.delete(s)
    audit(db, request, "delete", "shift", s.alias)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------

@router.get("/schedules")
def list_schedules(employee_id: int | None = None, q: str = "", db: Session = Depends(get_db),
                   offset: int = 0, limit: int = 200,
                   _=Depends(require("attendance.view"))):
    stmt = select(m.Schedule, m.Employee, m.Shift).join(m.Employee, m.Employee.id == m.Schedule.employee_id).join(
        m.Shift, m.Shift.id == m.Schedule.shift_id)
    if employee_id:
        stmt = stmt.where(m.Schedule.employee_id == employee_id)
    if q:
        stmt = stmt.where(_employee_search(q))
    total, rows = page(db, stmt.order_by(m.Employee.emp_code, m.Schedule.start_date, m.Schedule.id), offset, limit)
    return {"total": total, "rows": [ser(s) | {"emp_code": e.emp_code, "name": e.display_name,
                                                   "department": e.department.label if e.department else "",
                                                   "shift": sh.alias} for s, e, sh in rows]}


@router.post("/schedules")
def assign_schedule(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                    _=Depends(require("attendance.edit"))):
    """Assign a shift to employees (or whole departments) for a date range.
    Overlapping existing assignments are trimmed."""
    shift_id = int(data.get("shift_id") or 0)
    if not db.get(m.Shift, shift_id):
        raise HTTPException(422, "shift required")
    start = parse_date(data.get("start_date"))
    end = parse_date(data.get("end_date"))
    if not start or not end or end < start:
        raise HTTPException(422, "valid date range required")
    emp_ids = {int(x) for x in data.get("employee_ids") or []}
    dept_ids = [int(x) for x in data.get("department_ids") or []]
    if dept_ids:
        emp_ids |= set(db.scalars(select(m.Employee.id).where(m.Employee.department_id.in_(dept_ids),
                                                              m.Employee.status == "active")).all())
    if not emp_ids:
        raise HTTPException(422, "choose employees or departments")
    if set(db.scalars(select(m.Employee.id).where(m.Employee.id.in_(emp_ids)))) != emp_ids:
        raise HTTPException(422, "unknown employee")
    existing = defaultdict(list)
    for old in db.scalars(select(m.Schedule).where(
            m.Schedule.employee_id.in_(emp_ids), m.Schedule.start_date <= end, m.Schedule.end_date >= start)):
        existing[old.employee_id].append(old)
    for emp_id in sorted(emp_ids):
        for old in existing[emp_id]:
            anchor = old.cycle_anchor or old.start_date
            old.cycle_anchor = anchor
            if old.start_date >= start and old.end_date <= end:
                db.delete(old)
            elif old.start_date < start and old.end_date > end:
                db.add(m.Schedule(employee_id=emp_id, shift_id=old.shift_id,
                                  start_date=end + timedelta(days=1), end_date=old.end_date, cycle_anchor=anchor))
                old.end_date = start - timedelta(days=1)
            elif old.start_date < start:
                old.end_date = start - timedelta(days=1)
            else:
                old.start_date = end + timedelta(days=1)
        db.add(m.Schedule(employee_id=emp_id, shift_id=shift_id, start_date=start, end_date=end, cycle_anchor=start))
    audit(db, request, "assign", "schedule", f"{len(emp_ids)} employees")
    db.commit()
    return {"ok": True, "employees": len(emp_ids)}


@router.delete("/schedules/{sched_id}")
def delete_schedule(sched_id: int, request: Request, db: Session = Depends(get_db),
                    _=Depends(require("attendance.edit"))):
    s = db.get(m.Schedule, sched_id)
    if not s:
        raise HTTPException(404, "not found")
    db.delete(s)
    audit(db, request, "delete", "schedule", str(sched_id))
    db.commit()
    return {"ok": True}


def _dsched_dict(db, s):
    d = ser(s)
    dep = db.get(m.Department, s.department_id)
    sh = db.get(m.Shift, s.shift_id)
    d["department"] = dep.name if dep else ""
    d["shift"] = sh.alias if sh else ""
    return d


router.include_router(crud_router(m.DeptSchedule, "/dept-schedules", "attendance.view", "attendance.edit",
                                  order=m.DeptSchedule.start_date.desc(), to_dict=_dsched_dict))


@router.get("/temp-schedules")
def list_temp(start: str = "", end: str = "", employee_id: int | None = None, q: str = "",
              offset: int = 0, limit: int = 200, db: Session = Depends(get_db),
              _=Depends(require("attendance.view"))):
    stmt = select(m.TempSchedule, m.Employee).join(m.Employee, m.Employee.id == m.TempSchedule.employee_id)
    if start:
        stmt = stmt.where(m.TempSchedule.att_date >= parse_date(start))
    if end:
        stmt = stmt.where(m.TempSchedule.att_date <= parse_date(end))
    if employee_id:
        stmt = stmt.where(m.TempSchedule.employee_id == employee_id)
    if q:
        stmt = stmt.where(_employee_search(q))
    names = {t.id: t.alias for t in db.scalars(select(m.TimeTable)).all()}
    total, rows = page(db, stmt.order_by(m.TempSchedule.att_date.desc(), m.Employee.emp_code, m.TempSchedule.id), offset, limit)
    return {"total": total, "rows": [ser(t) | {"emp_code": e.emp_code, "name": e.display_name,
                                                   "timetable": names.get(t.timetable_id, "—")} for t, e in rows]}


@router.post("/temp-schedules")
def set_temp(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
             _=Depends(require("attendance.edit"))):
    """Replace the day's schedule for employees; timetable_ids=[] means day off."""
    emp_ids = sorted({int(x) for x in data.get("employee_ids") or []})
    start = parse_date(data.get("start_date") or data.get("att_date"))
    end = parse_date(data.get("end_date")) or start
    if not emp_ids or not start or end < start or (end - start).days > 366:
        raise HTTPException(422, "employees and a valid date range required")
    tt_ids = sorted({int(x) for x in data.get("timetable_ids") or []})
    if set(db.scalars(select(m.Employee.id).where(m.Employee.id.in_(emp_ids)))) != set(emp_ids):
        raise HTTPException(422, "unknown employee")
    if tt_ids and set(db.scalars(select(m.TimeTable.id).where(m.TimeTable.id.in_(tt_ids)))) != set(tt_ids):
        raise HTTPException(422, "unknown timetable")
    db.execute(delete(m.TempSchedule).where(m.TempSchedule.employee_id.in_(emp_ids),
                                           m.TempSchedule.att_date >= start, m.TempSchedule.att_date <= end))
    batch = []
    d = start
    while d <= end:
        for emp_id in emp_ids:
            for tt in (tt_ids or [None]):
                batch.append({"employee_id": emp_id, "att_date": d, "timetable_id": tt})
                if len(batch) == 5000:
                    db.execute(insert(m.TempSchedule), batch)
                    batch.clear()
        d += timedelta(days=1)
    if batch:
        db.execute(insert(m.TempSchedule), batch)
    audit(db, request, "set", "temp_schedule", f"{len(emp_ids)} employees {start}..{end}")
    db.commit()
    return {"ok": True}


@router.delete("/temp-schedules/{tid}")
def delete_temp(tid: int, db: Session = Depends(get_db), _=Depends(require("attendance.edit"))):
    t = db.get(m.TempSchedule, tid)
    if t:
        db.delete(t)
        db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Leave / manual punch / overtime (with approval)
# --------------------------------------------------------------------------

def _with_emp(db, obj):
    d = ser(obj)
    e = db.get(m.Employee, obj.employee_id)
    d["emp_code"] = e.emp_code if e else ""
    d["name"] = e.display_name if e else ""
    d["department"] = e.department.label if e and e.department else ""
    if isinstance(obj, m.Leave):
        lt = db.get(m.LeaveType, obj.leave_type_id)
        d["leave_type"] = lt.name if lt else ""
    return d


def _check_range(db, obj, data, is_new):
    if not obj.employee_id or not db.get(m.Employee, obj.employee_id):
        raise HTTPException(422, "employee required")
    if isinstance(obj, m.ManualLog) and not obj.punch_time:
        raise HTTPException(422, "punch time required")
    if hasattr(obj, "end_time") and (not obj.start_time or not obj.end_time or obj.end_time <= obj.start_time):
        raise HTTPException(422, "end must be after start")
    if obj.status not in m.APPROVAL_STATES:
        raise HTTPException(422, "invalid status")
    if isinstance(obj, m.Leave):
        if not obj.leave_type_id or not db.get(m.LeaveType, obj.leave_type_id):
            raise HTTPException(422, "leave type required")
        if obj.status != "rejected":
            clash = select(m.Leave.id).where(m.Leave.employee_id == obj.employee_id,
                                             m.Leave.status != "rejected", m.Leave.start_time < obj.end_time,
                                             m.Leave.end_time > obj.start_time)
            if obj.id is not None:
                clash = clash.where(m.Leave.id != obj.id)
            if db.scalar(clash.limit(1)):
                raise HTTPException(409, "employee already has leave or a request in this period")


def _approval_guard(db, request, user, obj, changes, action):
    """Approval permission protects decisions and immutable decision metadata."""
    can_approve = "attendance.approve" in user_permissions(user)
    if action == "delete":
        if not can_approve and obj.status != "pending":
            raise HTTPException(403, "permission required: attendance.approve")
        return
    for key in ("approver", "decided_at", "source"):
        changes.pop(key, None)
    requested_status = changes.get("status")
    if requested_status is not None and requested_status not in m.APPROVAL_STATES:
        raise HTTPException(422, "invalid status")
    if not can_approve:
        if requested_status in ("approved", "rejected"):
            raise HTTPException(403, "permission required: attendance.approve")
        # An editor's changed request must be reviewed again, including requests
        # whose previously approved status was omitted from the update payload.
        changes["status"] = "pending"
    effective_status = changes.get("status", obj.status)
    if action == "create":
        changes["source"] = "admin"
    changes["approver"] = user.username if effective_status in ("approved", "rejected") else ""
    changes["decided_at"] = now() if effective_status in ("approved", "rejected") else None


for _model, _path in ((m.Leave, "/leaves"), (m.ManualLog, "/manual-logs"), (m.Overtime, "/overtimes")):
    router.include_router(crud_router(_model, _path, "attendance.view", "attendance.edit",
                                      order=_model.id.desc(), to_dict=_with_emp, before_save=_check_range,
                                      mutation_guard=_approval_guard,
                                      filters=("employee_id", "status")))


@router.post("/approvals/{kind}")
def approve(kind: str, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
            user=Depends(require("attendance.approve"))):
    model = {"leaves": m.Leave, "manual-logs": m.ManualLog, "overtimes": m.Overtime}.get(kind)
    status = data.get("status")
    if model is None or status not in ("approved", "rejected", "pending"):
        raise HTTPException(422, "invalid request")
    try:
        ids = sorted({int(x) for x in data.get("ids", [])})
    except (TypeError, ValueError):
        raise HTTPException(422, "invalid request ids")
    objects = list(db.scalars(select(model).where(model.id.in_(ids)).order_by(model.id)))
    # Apply the complete proposed state before validating. A conflicting batch
    # rolls back as one transaction instead of leaving the first item approved.
    for obj in objects:
        obj.status = status
    if model is m.Leave and status != "rejected":
        db.flush()
        for obj in objects:
            _check_range(db, obj, {}, False)
    from .. import alerts
    akind = {"leaves": "leave", "manual-logs": "manual", "overtimes": "overtime"}[kind]
    for obj in objects:
        obj.approver = user.username if status != "pending" else ""
        obj.decided_at = now() if status != "pending" else None
        db.flush()
        alerts.request_decided(db, akind, obj)
    audit(db, request, status, kind, ",".join(map(str, ids))[:200])
    db.commit()
    return {"ok": True, "count": len(objects)}


# --------------------------------------------------------------------------
# Calculated attendance
# --------------------------------------------------------------------------

@router.get("/attendance/policies")
def list_policies(date: str = "", scope: str = "", target_id: int | None = None,
                  offset: int = 0, limit: int = 200, db: Session = Depends(get_db),
                  _=Depends(require("attendance.view"))):
    day = parse_date(date, now().date())
    if scope and scope not in ("rules", "timetable"):
        raise HTTPException(422, "scope must be rules or timetable")
    stmt = select(policies.AttendancePolicy)
    if scope:
        stmt = stmt.where(policies.AttendancePolicy.scope == scope)
    if target_id is not None:
        stmt = stmt.where(policies.AttendancePolicy.target_id == target_id)
    total, rows = page(db, stmt.order_by(policies.AttendancePolicy.created_at.desc(),
                                       policies.AttendancePolicy.id.desc()), offset, limit)
    timeline = policies.PolicyTimeline(db)
    rule_revision = timeline.at("rules", 0, day)
    current_rules = {"snapshot": rule_revision["snapshot"] if rule_revision else policies.rules_snapshot(db),
                     "version": rule_revision["id"] if rule_revision else None,
                     "effective_from": rule_revision["effective_from"] if rule_revision else None}
    timetables = []
    for tt in db.scalars(select(m.TimeTable).order_by(m.TimeTable.alias)):
        revision = timeline.at("timetable", tt.id, day)
        timetables.append({"id": tt.id, **(revision["snapshot"] if revision else policies.timetable_snapshot(tt)),
                           "version": revision["id"] if revision else None,
                           "effective_from": revision["effective_from"] if revision else None})
    return {"total": total, "rows": [policies.serialize_policy(row[0]) for row in rows],
            "current": {"rules": current_rules, "timetables": timetables}, "date": day.isoformat()}


@router.post("/attendance/policies")
def save_policy(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                user=Depends(require("attendance.edit"))):
    from sqlalchemy.exc import IntegrityError
    try:
        prepared = policies.prepare_revision(db, data)
        revision = policies.append_revision(db, prepared, user.username)
        audit(db, request, "policy.revise", "attendance_policy", str(revision.id))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "duplicate timetable name or invalid policy reference")
    return policies.serialize_policy(revision)


@router.post("/attendance/policies/preview")
def preview_policy(data: dict = Body(...), db: Session = Depends(get_db),
                   _=Depends(require("attendance.view"))):
    try:
        prepared = policies.prepare_revision(db, data, preview=True)
        start = policies._strict_date(data.get("start"))
        end = policies._strict_date(data.get("end"))
        if end < start or (end - start).days > 400:
            raise ValueError("preview range must contain at most 401 days")
        filters = {}
        for key in ("employee_ids", "department_ids"):
            values = data.get(key)
            if values is not None:
                if (not isinstance(values, list) or len(values) > 5000 or
                        any(type(value) is not int or value < 1 for value in values)):
                    raise ValueError(f"{key} must be a list of positive integer IDs")
                filters[key] = values
        count, _unused = employee_day_page(db, start, end, limit=0, **filters)
        if count > 5000:
            raise ValueError("preview limit is 5000 employee-days; select fewer employees or dates")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    override = {**prepared, "id": 2**63, "baseline": False}
    original = Engine(db, start, end, **filters)
    proposed = Engine(db, start, end, **filters, policy_overrides=[override])
    metrics = ("required", "worked", "late", "early", "absent", "leave", "ot")
    totals_before, totals_after = {key: 0 for key in metrics}, {key: 0 for key in metrics}
    changed, examples = 0, []
    for before, after in zip(original.iter_rows(), proposed.iter_rows()):
        old = {key: getattr(before, key) for key in metrics} | {"status": before.status}
        new = {key: getattr(after, key) for key in metrics} | {"status": after.status}
        for key in metrics:
            totals_before[key] += old[key]
            totals_after[key] += new[key]
        if old != new:
            changed += 1
            if len(examples) < 50:
                examples.append({"employee_id": before.employee_id, "emp_code": before.emp_code,
                                 "name": before.name, "date": before.att_date.isoformat(),
                                 "before": old, "after": new})
    return {"scanned_employee_days": count, "changed_employee_days": changed,
            "before_totals": totals_before, "after_totals": totals_after, "examples": examples,
            "limited_examples": changed > len(examples), "effective_from": prepared["effective_from"],
            "scope": prepared["scope"], "target_id": prepared["target_id"]}


_PUNCH_REASONS = {
    "selected_check_in": ("استخدمت كدخول", "Used as check-in"),
    "selected_check_out": ("استخدمت كخروج", "Used as check-out"),
    "selected_boundary": ("استخدمت كحد لفترة حضور", "Used as an attendance boundary"),
    "duplicate_window": ("مستبعدة وفق نافذة تكرار البصمات", "Excluded by the duplicate-punch window"),
    "non_attendance_device": ("الجهاز غير مخصص لحساب الحضور", "Device is not enabled for attendance calculation"),
    "manual_not_approved": ("التصحيح اليدوي غير معتمد", "Manual correction is not approved"),
    "additional_punch": ("حركة إضافية؛ لم تُختر كدخول أو خروج", "Additional punch; not chosen as an entry or exit"),
    "outside_day_window": ("خارج نافذة هذا اليوم أو تابعة لدوام مجاور", "Outside this day's window or owned by a neighboring shift"),
}


@router.get("/attendance/explain/{employee_id}")
def explain_attendance(employee_id: int, date: str = "", db: Session = Depends(get_db),
                       _=Depends(require("attendance.view"))):
    from ..timekeeping import wall_time_info
    day = parse_date(date, now().date())
    employee = db.get(m.Employee, employee_id)
    if employee is None:
        raise HTTPException(404, "employee not found")
    calculator = Engine(db, day, day, employee_ids=[employee_id])
    if not calculator.employees:
        raise HTTPException(422, "employee is outside the attendance-enabled employment period")
    result = calculator.day(calculator.employees[0], day, now().date())
    rules = calculator.rules_for(day)
    rule_revision = calculator.policy_timeline.at("rules", 0, day)
    values = {"att." + key: list(value) if isinstance(value, tuple) else value
              for key, value in vars(rules).items()}
    tts = calculator.timetables_for(employee, day)
    schedule = None
    source = "none"
    if (employee.id, day) in calculator.temp:
        source = "temporary"
        schedule = {"date": day.isoformat(), "timetable_ids": calculator.temp[(employee.id, day)]}
    else:
        for candidate_source, candidates in (("employee", calculator.schedules.get(employee.id, [])),
                                              ("department", calculator.dept_schedules.get(employee.department_id, []))):
            matches = [item for item in candidates if item.start_date <= day <= item.end_date]
            if matches:
                item = max(matches, key=lambda candidate: candidate.start_date)
                source, schedule = candidate_source, ser(item)
                break
    timetable_values = []
    for tt in tts or []:
        revision = calculator.policy_timeline.at("timetable", tt.id, day)
        timetable_values.append({"id": tt.id, **policies.timetable_snapshot(tt),
                                 "version": revision["id"] if revision else None,
                                 "effective_from": revision["effective_from"] if revision else None})
    lo, hi = datetime.combine(day - timedelta(days=1), time.min), datetime.combine(day + timedelta(days=2), time.min)
    device_rows = {row.sn: row for row in db.scalars(select(m.Device))}
    transaction_stmt = select(m.Transaction.id, m.Transaction.punch_time, m.Transaction.device_sn,
                              m.Transaction.source, m.Transaction.punch_state).where(
        m.Transaction.employee_id == employee_id, m.Transaction.punch_time >= lo, m.Transaction.punch_time < hi
    ).order_by(m.Transaction.punch_time, m.Transaction.id)
    raw_sources = [{"id": row.id, "kind": "transaction", "stamp": row.punch_time,
                    "device_sn": row.device_sn, "source": row.source, "punch_state": row.punch_state,
                    "status": "recorded", "approver": "", "decided_at": None}
                   for row in db.execute(transaction_stmt.limit(5001))]
    manual_stmt = select(m.ManualLog).where(m.ManualLog.employee_id == employee_id,
        m.ManualLog.punch_time >= lo, m.ManualLog.punch_time < hi).order_by(m.ManualLog.punch_time, m.ManualLog.id)
    raw_sources += [{"id": row.id, "kind": "manual", "stamp": row.punch_time, "device_sn": "",
                     "source": row.source or "manual", "punch_state": row.punch_state, "status": row.status,
                     "approver": row.approver, "decided_at": row.decided_at.isoformat() if row.decided_at else None,
                     "correction_reason": row.reason}
                    for row in db.scalars(manual_stmt.limit(5001))]
    raw_sources.sort(key=lambda item: (item["stamp"], item["kind"] != "transaction", item["id"]))
    raw_limit_hit = len(raw_sources) > 5000
    raw_sources = raw_sources[:5000]
    selected_in = {seg.clock_in for seg in result.segments if seg.clock_in}
    selected_out = {seg.clock_out for seg in result.segments if seg.clock_out}
    if not result.segments:
        selected_in = {result.clock_in} if result.clock_in else set()
        selected_out = {result.clock_out} if result.clock_out else set()
    kept = calculator._punches_for(employee_id, day)
    kept_set, day_punches = set(kept), set(result.punches)
    seen_times, punch_rows, warnings = set(), [], []
    for item in raw_sources:
        stamp = item.pop("stamp")
        terminal = device_rows.get(item["device_sn"])
        item["device"] = terminal.alias if terminal else item["device_sn"]
        eligible = item["kind"] == "manual" and item["status"] == "approved"
        if item["kind"] == "transaction":
            eligible = not item["device_sn"] or bool(terminal and terminal.is_attendance)
        if item["kind"] == "manual" and not eligible:
            reason = "manual_not_approved"
        elif not eligible:
            reason = "non_attendance_device"
        elif stamp not in kept_set or stamp in seen_times:
            reason = "duplicate_window"
        elif stamp in selected_in and stamp in selected_out:
            reason = "selected_boundary"
        elif stamp in selected_in:
            reason = "selected_check_in"
        elif stamp in selected_out:
            reason = "selected_check_out"
        elif stamp in day_punches:
            reason = "additional_punch"
        else:
            reason = "outside_day_window"
        if eligible:
            seen_times.add(stamp)
        clock_info = wall_time_info(stamp)
        if clock_info["kind"] in ("ambiguous", "nonexistent"):
            warnings.append({"code": "clock_" + clock_info["kind"], "time": stamp.isoformat(),
                             "message_ar": "وقت محلي يحتاج مراجعة بسبب انتقال التوقيت الصيفي",
                             "message_en": "Local time needs review at a daylight-saving transition"})
        punch_rows.append({**item, "time": stamp.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason,
                           "reason_ar": _PUNCH_REASONS[reason][0], "reason_en": _PUNCH_REASONS[reason][1],
                           "selected": reason.startswith("selected_"),
                           "state_label": m.PUNCH_STATES.get(item["punch_state"], "unknown"), "clock": clock_info})
    warnings.append({"code": "state_is_metadata",
                     "message_ar": "نوع الحركة الوارد من الجهاز يُعرض كدليل؛ اختيار الدخول والخروج يعتمد نوافذ الدوام لأن إعدادات الأجهزة قد تختلف",
                     "message_en": "Device punch state is evidence; entry and exit selection uses timetable windows because terminal configurations differ"})
    if raw_limit_hit:
        warnings.append({"code": "source_limit", "message_ar": "عُرضت أول 5000 حركة؛ الحساب يستعمل جميع الحركات المؤهلة",
                         "message_en": "Only the first 5000 source records are shown; calculation uses all eligible punches"})
    decisions = [{"code": "schedule", "message_ar": "أولوية الدوام: مؤقت ثم موظف ثم قسم؛ الحساب بالدقائق المحلية",
                   "message_en": "Schedule priority: temporary, employee, department; durations use local wall-clock minutes"},
                 {"code": "required", "value": result.required,
                  "message_ar": f"الدقائق المطلوبة بعد طرح الاستراحة: {result.required}",
                  "message_en": f"Required minutes after scheduled breaks: {result.required}"},
                 {"code": "worked", "value": result.worked,
                  "message_ar": f"دقائق العمل المحتسبة بعد الاستراحة والتقريب: {result.worked}",
                  "message_en": f"Counted work minutes after breaks and rounding: {result.worked}"},
                 {"code": "late", "value": result.late,
                  "message_ar": f"التأخير بعد تطبيق السماح والإجازات المعتمدة: {result.late}",
                  "message_en": f"Late minutes after grace and approved leave: {result.late}"},
                 {"code": "early", "value": result.early,
                  "message_ar": f"الخروج المبكر بعد تطبيق السماح والإجازات المعتمدة: {result.early}",
                  "message_en": f"Early minutes after grace and approved leave: {result.early}"}]
    proof_lo = result.sched_in or datetime.combine(day, time.min)
    proof_hi = result.sched_out or proof_lo + timedelta(days=1)
    approvals = {}
    for key, model in (("leaves", m.Leave), ("overtimes", m.Overtime)):
        approvals[key] = [ser(row) for row in db.scalars(select(model).where(
            model.employee_id == employee_id, model.start_time < proof_hi, model.end_time > proof_lo))]
    segments = []
    for segment in result.segments:
        segment_values = vars(segment).copy()
        for key in ("sched_in", "sched_out", "clock_in", "clock_out"):
            value = segment_values[key]
            segment_values[key] = value.strftime("%Y-%m-%d %H:%M:%S") if value else ""
        segments.append(segment_values)
    return {"result": result.as_dict(), "rules": {"values": values,
            "version": rule_revision["id"] if rule_revision else None,
            "effective_from": rule_revision["effective_from"] if rule_revision else None},
            "schedules": {"source": source, "schedule": schedule, "timetables": timetable_values},
            "punches": punch_rows, "decisions": decisions, "warnings": warnings,
            "approvals": approvals, "segments": segments, "duration_mode": "wall"}

@router.get("/attendance/daily")
def daily(start: str = "", end: str = "", employee_ids: str = "", department_ids: str = "",
          status: str = "", q: str = "", offset: int = 0, limit: int = 1000,
          db: Session = Depends(get_db), _=Depends(require("attendance.view"))):
    today = now().date()
    ds = parse_date(start, today)
    de = parse_date(end, ds)
    if de < ds or (de - ds).days > 400:
        raise HTTPException(422, "invalid range (max 400 days)")
    wanted = {x.strip() for x in status.split(",") if x.strip()}
    off, lim = max(0, offset), max(1, min(limit, 5000))
    filters = dict(employee_ids=ids_param(employee_ids), department_ids=ids_param(department_ids), q=q[:100])
    if not wanted:
        total, days = employee_day_page(db, ds, de, offset=off, limit=lim, **filters)
        out = [r.as_dict() for r in days]
        return {"total": total, "offset": off, "limit": lim, "has_more": off + len(out) < total, "rows": out}
    out, total = [], 0
    for r in iter_attendance(db, ds, de, **filters):
        if wanted and r.status not in wanted:
            continue
        total += 1
        if total > off and len(out) < lim:
            out.append(r.as_dict())
    return {"total": total, "offset": off, "limit": lim, "has_more": off + len(out) < total, "rows": out}


@router.get("/attendance/summary")
def summary(start: str = "", end: str = "", employee_ids: str = "", department_ids: str = "",
            db: Session = Depends(get_db), _=Depends(require("attendance.view"))):
    today = now().date()
    ds = parse_date(start, today.replace(day=1))
    de = parse_date(end, today)
    if de < ds or (de - ds).days > 400:
        raise HTTPException(422, "invalid range (max 400 days)")
    rows = iter_attendance(db, ds, de, employee_ids=ids_param(employee_ids),
                           department_ids=ids_param(department_ids))
    return {"rows": summarize(rows)}


@router.get("/attendance/calendar/{emp_id}")
def calendar(emp_id: int, month: str = "", db: Session = Depends(get_db), _=Depends(require("attendance.view"))):
    """One employee's month (for the schedule / attendance calendar view)."""
    today = now().date()
    first = parse_date(month + "-01" if month and len(month) == 7 else month, today.replace(day=1)).replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    rows = Engine(db, first, last, employee_ids=[emp_id]).run()
    return {"month": first.strftime("%Y-%m"), "days": [r.as_dict() for r in rows],
            "summary": (summarize(rows) or [{}])[0]}
