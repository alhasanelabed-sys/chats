"""Employee portal (self-service): ``/me`` in the browser, ``/api/me/*`` here.

An employee signs in with their personnel number and a portal password given by HR,
sees today, their month day by day, their balances, sends leave / punch-correction /
overtime requests and follows them, and receives their notifications.
"""
from __future__ import annotations

import os
import secrets
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import models as m
from ..photos import with_photo
from .. import store
from ..db import get_db, now
from ..config import settings
from ..engine import Engine
from ..security import (LoginThrottle, hash_password, make_token, read_token, token_password_matches,
                        validate_password, verify_password)
from ..portal_gate import client_ip, is_https
from .deps import audit, require

router = APIRouter(prefix="/api")
COOKIE = os.environ.get("HADER_PORTAL_SESSION_COOKIE", "emp_session")
MAX_FAILS, MAX_IP_FAILS, FAIL_WINDOW = 5, 20, 600
_LOGIN_THROTTLE = LoginThrottle(MAX_FAILS, MAX_IP_FAILS, FAIL_WINDOW)
_FAILS, _IP_FAILS = _LOGIN_THROTTLE.accounts, _LOGIN_THROTTLE.addresses
_PASSWORD_CHANGE_PATHS = {("GET", "/api/me"), ("POST", "/api/me/password"),
                          ("POST", "/api/me/logout"), ("POST", "/api/me/lang")}


def _set_session(response: Response, request: Request, e: m.Employee) -> None:
    response.set_cookie(COOKIE, make_token(e.id, e.portal_hash, kind="e"), httponly=True,
                        samesite="lax", max_age=3600 * settings.session_hours, path="/", secure=is_https(request))


def current_employee(request: Request, db: Session = Depends(get_db)) -> m.Employee:
    data = read_token(request.cookies.get(COOKIE, "") or "")
    if not data or data.get("k") != "e":
        raise HTTPException(401, "not signed in")
    e = db.get(m.Employee, data["u"])
    if not e or e.status != "active" or not e.portal_enabled or not token_password_matches(data, e.portal_hash):
        raise HTTPException(401, "not signed in")
    if not store.get(db, "portal.enabled"):
        raise HTTPException(403, "the portal is switched off")
    if e.portal_must_change and (request.method, request.url.path) not in _PASSWORD_CHANGE_PATHS:
        raise HTTPException(403, {"code": "password_change_required",
                                  "message": "غيّر كلمة المرور أولاً / Change your password first"})
    return e


# --------------------------------------------------------------------------
# Sign in
# --------------------------------------------------------------------------

@router.post("/me/login")
def login(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db)):
    code = str(data.get("code") or "").strip()[:30]
    pw = str(data.get("password") or "")
    ip = client_ip(request)
    if not _LOGIN_THROTTLE.check(code, ip):
        raise HTTPException(429, "محاولات كثيرة، حاول بعد 10 دقائق / Too many attempts, try again in 10 minutes")
    if not store.get(db, "portal.enabled"):
        raise HTTPException(403, "البوابة متوقفة / The portal is switched off")
    e = db.scalar(select(m.Employee).where(m.Employee.emp_code == code))
    if not e or e.status != "active" or not e.portal_enabled or not e.portal_hash or not verify_password(pw, e.portal_hash):
        _LOGIN_THROTTLE.failure(code, ip)
        raise HTTPException(401, "الرقم أو كلمة المرور غير صحيحة / Wrong number or password")
    _LOGIN_THROTTLE.success(code, ip)
    e.portal_last_login = now()
    db.commit()
    _set_session(response, request, e)
    return {"ok": True, "must_change": bool(e.portal_must_change)}


@router.post("/me/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/me/password")
def change_password(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db),
                    e: m.Employee = Depends(current_employee)):
    old, new = str(data.get("old") or ""), str(data.get("new") or "")
    if not verify_password(old, e.portal_hash or ""):
        raise HTTPException(422, "كلمة المرور الحالية غير صحيحة / Current password is wrong")
    try:
        validate_password(new, identity=e.emp_code, old_password=old)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    e.portal_hash = hash_password(new)
    e.portal_must_change = False
    db.commit()
    _set_session(response, request, e)
    return {"ok": True}


@router.post("/me/lang")
def set_lang(data: dict = Body(...), db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    e.portal_lang = "en" if data.get("lang") == "en" else "ar"
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# What the employee sees
# --------------------------------------------------------------------------

def _day(r) -> dict:
    d = r.as_dict()
    return {k: d.get(k) for k in ("date", "status", "timetable", "sched_in", "sched_out", "clock_in", "clock_out",
                                  "late", "early", "worked", "ot", "leave", "holiday", "punches", "exceptions",
                                  "required", "absent", "workday", "present")} | {"date": r.att_date.isoformat()}


@router.get("/me")
def me(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    today = now().date()
    rows = Engine(db, today, today, employee_ids=[e.id]).run()
    t = _day(rows[0]) if rows else None
    unread = db.scalar(select(func.count()).select_from(m.Notification).where(
        m.Notification.to_kind == "employee", m.Notification.to_id == e.id, m.Notification.read_at.is_(None))) or 0
    pending = sum(db.scalar(select(func.count()).select_from(mod).where(
        mod.employee_id == e.id, mod.status == "pending")) or 0 for mod in (m.Leave, m.ManualLog, m.Overtime))
    last = db.scalar(select(m.Transaction).where(m.Transaction.employee_id == e.id)
                     .order_by(m.Transaction.punch_time.desc()).limit(1))
    return {
        "code": e.emp_code, "name": e.full_name, "name_en": e.name_en or "", "display_name": e.display_name,
        "department": e.department.label if e.department else "", "position": e.position.label if e.position else "",
        "has_photo": bool(e.photo) or bool(with_photo(db, [e.id])), "lang": e.portal_lang or "ar", "must_change": bool(e.portal_must_change),
        "company": store.get(db, "company.name_ar") or store.get(db, "company.name") or "",
        "today": t, "unread": unread, "pending": pending,
        "last_punch": last.punch_time.strftime("%Y-%m-%d %H:%M") if last else "",
        "requests_on": bool(store.get(db, "portal.requests")),
    }


@router.get("/me/photo")
def my_photo(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    from ..photos import photo_bytes
    data = photo_bytes(db, e)
    if not data:
        raise HTTPException(404, "no photo")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/me/month")
def my_month(ym: str = "", db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    today = now().date()
    try:
        y, mo = (int(x) for x in ym.split("-")) if ym else (today.year, today.month)
        first = date(y, mo, 1)
    except ValueError:
        raise HTTPException(422, "ym must be YYYY-MM")
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end = min(last, today)
    days = [_day(r) for r in Engine(db, first, end, employee_ids=[e.id]).run()] if first <= today else []
    tot = {"worked": 0, "late": 0, "early": 0, "ot": 0, "present_days": 0, "absent_days": 0, "leave_days": 0,
           "late_days": 0, "required": 0, "absent_minutes": 0, "partial_absent_days": 0}
    for d in days:
        tot["worked"] += d["worked"] or 0
        tot["late"] += d["late"] or 0
        tot["early"] += d["early"] or 0
        tot["ot"] += d["ot"] or 0
        tot["required"] += d["required"] or 0
        tot["present_days"] += 1 if d["clock_in"] or d["clock_out"] else 0
        tot["absent_days"] += 1 if d["status"] == "absent" else 0
        tot["absent_minutes"] += d["absent"] or 0
        tot["partial_absent_days"] += 1 if d["status"] == "partial_absent" else 0
        tot["leave_days"] += 1 if d["status"] == "leave" else 0
        tot["late_days"] += 1 if d["late"] else 0
    return {"month": first.strftime("%Y-%m"), "first": first.isoformat(), "last": last.isoformat(),
            "days": days, "totals": tot}


@router.get("/me/balances")
def my_balances(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    year = now().year
    a, b = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    calculator = Engine(db, a.date() - timedelta(days=1), b.date() - timedelta(days=1), employee_ids=[e.id])
    approved = {}
    for lv in db.scalars(select(m.Leave).where(m.Leave.employee_id == e.id, m.Leave.status == "approved",
                                               m.Leave.end_time > a, m.Leave.start_time < b)):
        approved.setdefault(lv.leave_type_id, []).append((max(a, lv.start_time), min(b, lv.end_time)))
    pending_counts = dict(db.execute(select(m.Leave.leave_type_id, func.count()).where(
        m.Leave.employee_id == e.id, m.Leave.status == "pending").group_by(m.Leave.leave_type_id)).all())
    out = []
    for lt in db.scalars(select(m.LeaveType).order_by(m.LeaveType.code)).all():
        used = _scheduled_leave_days(calculator, e, approved.get(lt.id, []), a, b)
        pending = pending_counts.get(lt.id, 0)
        out.append({"id": lt.id, "name": lt.label, "color": lt.color, "paid": lt.paid, "portal": lt.portal is not False,
                    "entitled": lt.annual_days or 0, "used": round(used, 2),
                    "left": round((lt.annual_days or 0) - used, 2) if lt.annual_days else None, "pending": pending})
    return {"year": year, "rows": out, "basis": "scheduled_workdays",
            "unscheduled_basis": "configured_weekend_and_8_hour_partial_day"}


def _merge_periods(periods: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged = []
    for start, end in sorted(periods):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _scheduled_leave_days(calculator: Engine, employee: m.Employee, periods, lo: datetime, hi: datetime) -> float:
    """Charge only work intervals, including overnight shifts crossing a year boundary.

    Existing installations without schedules use their configured weekend and the
    legacy eight-hour fractional-day convention, explicitly reported by the API.
    This is an attendance balance, not a payroll or carry-over policy.
    """
    periods = _merge_periods([(max(lo, start), min(hi, end)) for start, end in periods])
    if not periods:
        return 0.0

    def covered(start, end):
        return sum(max(0.0, (min(end, finish) - max(start, begin)).total_seconds() / 60)
                   for begin, finish in periods)

    used, day = 0.0, lo.date() - timedelta(days=1)
    while day < hi.date() or (day == hi.date() and hi.time() != time.min):
        if not calculator.holiday_for(employee, day):
            timetables = calculator.timetables_for(employee, day)
            if timetables is None:
                if day.weekday() not in calculator.rules.weekend:
                    start = datetime.combine(day, time.min)
                    used += min(1.0, covered(start, start + timedelta(days=1)) / 480)
            else:
                for timetable in timetables:
                    start, end = calculator._bounds(day, timetable)
                    required = (end - start).total_seconds() / 60
                    leave = covered(start, end)
                    brk = calculator._break(timetable, start)
                    if brk:
                        break_start, break_end = max(start, brk[0]), min(end, brk[1])
                        if break_end > break_start:
                            required -= (break_end - break_start).total_seconds() / 60
                            leave -= covered(break_start, break_end)
                    if timetable.kind == "flexible":
                        required = timetable.work_minutes or 480
                    if required > 0:
                        used += min(1.0, max(0.0, leave) / required) * (timetable.workday or 1.0)
        day += timedelta(days=1)
    return used


def _leave_days(lv: m.Leave) -> float:
    span = lv.end_time - lv.start_time
    if span >= timedelta(hours=20):  # whole days
        return float((lv.end_time.date() - lv.start_time.date()).days + (0 if lv.end_time.time() == time.min else 1))
    return round(span.total_seconds() / 3600 / 8, 2)  # part of a day, counted on an 8-hour day


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

KINDS = {"leave": m.Leave, "manual": m.ManualLog, "overtime": m.Overtime}


def _req(kind: str, o) -> dict:
    d = {"kind": kind, "id": o.id, "status": o.status, "reason": o.reason, "approver": o.approver,
         "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
         "decided_at": o.decided_at.strftime("%Y-%m-%d %H:%M") if o.decided_at else ""}
    if kind == "manual":
        d.update(time=o.punch_time.strftime("%Y-%m-%d %H:%M"), state=o.punch_state)
    else:
        d.update(start=o.start_time.strftime("%Y-%m-%d %H:%M"), end=o.end_time.strftime("%Y-%m-%d %H:%M"))
    if kind == "leave":
        d["leave_type_id"] = o.leave_type_id
        lt = o.__dict__.get("_lt")
        d["type"] = lt.label if lt else ""
        d["days"] = _leave_days(o)
        d["days_basis"] = "calendar_duration"
    return d


@router.get("/me/requests")
def my_requests(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    types = {t.id: t for t in db.scalars(select(m.LeaveType)).all()}
    out = []
    for kind, model in KINDS.items():
        for o in db.scalars(select(model).where(model.employee_id == e.id).order_by(model.id.desc()).limit(100)).all():
            if kind == "leave":
                o.__dict__["_lt"] = types.get(o.leave_type_id)
            out.append(_req(kind, o))
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return {"rows": out}


def _dt(v: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(v).replace("T", " ").strip())
        if result.tzinfo is not None:
            raise ValueError("attendance uses local wall-clock time")
        return result
    except ValueError:
        raise HTTPException(422, f"{field}: invalid date/time")


@router.post("/me/requests")
def new_request(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                e: m.Employee = Depends(current_employee)):
    if not store.get(db, "portal.requests"):
        raise HTTPException(403, "الطلبات متوقفة / Requests are switched off")
    kind = data.get("kind")
    reason = str(data.get("reason") or "").strip()[:250]
    if kind == "leave":
        lt = db.get(m.LeaveType, int(data.get("leave_type_id") or 0))
        if not lt or lt.portal is False:
            raise HTTPException(422, "نوع الإجازة غير متاح / Leave type not available")
        start, end = _dt(data.get("start"), "start"), _dt(data.get("end"), "end")
        if len(str(data.get("end"))) <= 10:   # date only = until the end of that day
            end = datetime.combine(end.date() + timedelta(days=1), time.min)
        if end <= start:
            raise HTTPException(422, "النهاية يجب أن تكون بعد البداية / End must be after start")
        clash = db.scalar(select(m.Leave.id).where(m.Leave.employee_id == e.id, m.Leave.status != "rejected",
                                                   m.Leave.start_time < end, m.Leave.end_time > start).limit(1))
        if clash:
            raise HTTPException(409, "لديك إجازة أو طلب في نفس الفترة / You already have leave in that period")
        o = m.Leave(employee_id=e.id, leave_type_id=lt.id, start_time=start, end_time=end, reason=reason)
    elif kind == "manual":
        t = _dt(data.get("time"), "time")
        if t > now() + timedelta(minutes=5):
            raise HTTPException(422, "لا يمكن تصحيح وقت في المستقبل / Cannot correct a future time")
        if t < now() - timedelta(days=int(store.get(db, "portal.correction_days") or 31)):
            raise HTTPException(422, "التاريخ قديم جداً / Too far in the past")
        o = m.ManualLog(employee_id=e.id, punch_time=t, punch_state=1 if str(data.get("state")) == "1" else 0,
                        reason=reason)
    elif kind == "overtime":
        start, end = _dt(data.get("start"), "start"), _dt(data.get("end"), "end")
        if end <= start or end - start > timedelta(hours=16):
            raise HTTPException(422, "الفترة غير صحيحة / Invalid period")
        o = m.Overtime(employee_id=e.id, start_time=start, end_time=end, reason=reason)
    else:
        raise HTTPException(422, "unknown request")
    o.status, o.source = "pending", "portal"
    db.add(o)
    db.flush()
    from .. import alerts
    alerts.request_submitted(db, kind, o, e)
    db.add(m.AuditLog(username=f"portal:{e.emp_code}", action=f"request.{kind}", target=str(o.id),
                      ip=request.client.host if request.client else ""))
    db.commit()
    from ..events import publish
    publish("people", what="request")
    return {"ok": True, "id": o.id}


@router.delete("/me/requests/{kind}/{rid}")
def cancel_request(kind: str, rid: int, db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    model = KINDS.get(kind)
    o = db.get(model, rid) if model else None
    if not o or o.employee_id != e.id:
        raise HTTPException(404, "not found")
    if o.status != "pending":
        raise HTTPException(409, "لا يمكن إلغاء طلب تم الرد عليه / An answered request cannot be cancelled")
    db.delete(o)
    db.commit()
    return {"ok": True}


@router.get("/me/leave-types")
def leave_types(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    return {"rows": [{"id": t.id, "name": t.label, "color": t.color} for t in
                     db.scalars(select(m.LeaveType).order_by(m.LeaveType.code)).all() if t.portal is not False]}


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

@router.get("/me/notifications")
def my_notifications(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    rows = db.scalars(select(m.Notification).where(m.Notification.to_kind == "employee", m.Notification.to_id == e.id)
                      .order_by(m.Notification.id.desc()).limit(100)).all()
    return {"rows": [{"id": n.id, "kind": n.kind, "level": n.level, "title": n.title, "body": n.body,
                      "time": n.created_at.strftime("%Y-%m-%d %H:%M"), "read": n.read_at is not None} for n in rows]}


@router.post("/me/notifications/read")
def read_notifications(db: Session = Depends(get_db), e: m.Employee = Depends(current_employee)):
    db.execute(update(m.Notification).where(m.Notification.to_kind == "employee", m.Notification.to_id == e.id,
                                            m.Notification.read_at.is_(None)).values(read_at=now()))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# HR side: open the portal for employees
# --------------------------------------------------------------------------

def _new_password() -> str:
    return secrets.token_urlsafe(12)


@router.post("/employees/portal")
def portal_access(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                  _=Depends(require("personnel.edit"))):
    """enable (new password for those without one) | reset (new password) | disable.
    Returns the passwords once, to hand out or print."""
    action = data.get("action")
    q = select(m.Employee).where(m.Employee.status == "active")
    if not data.get("all"):
        q = q.where(m.Employee.id.in_([int(x) for x in data.get("ids") or []]))
    out = []
    for e in db.scalars(q.order_by(m.Employee.emp_code)).all():
        if action == "disable":
            e.portal_enabled = False
            continue
        if action not in ("enable", "reset"):
            raise HTTPException(422, "unknown action")
        if action == "enable" and e.portal_enabled and e.portal_hash:
            continue
        pw = _new_password()
        e.portal_hash, e.portal_enabled, e.portal_must_change = hash_password(pw), True, True
        out.append({"emp_code": e.emp_code, "name": e.full_name, "department": e.department.label if e.department else "",
                    "password": pw})
    audit(db, request, f"portal.{action}", "employee", f"{len(out)} employees")
    db.commit()
    return {"rows": out}


@router.get("/portal/info")
def portal_info(request: Request, db: Session = Depends(get_db), _=Depends(require("personnel.view"))):
    """Where employees open the portal: inside the network, and on the internet when set up."""
    from ..config import settings
    from .. import portal_gate as G
    port = settings.portal_port if G.LISTENING else settings.web_port
    suffix = "" if G.LISTENING else "/me"
    lan = [f"http://{ip}:{port}{suffix}" for ip in G.lan_addresses()]
    public = (store.get(db, "portal.public_url") or "").strip().rstrip("/")
    return {"lan": lan, "public": public, "port": port, "portal_port": settings.portal_port if G.LISTENING else 0,
            "web_port": settings.web_port, "best": public or (lan[0] if lan else f"{request.base_url}me".rstrip("/")),
            "requests": bool(store.get(db, "portal.requests"))} | portal_stats(db)


@router.get("/portal/qr.svg")
def portal_qr(text: str, _=Depends(require("personnel.view"))):
    """A QR code for a portal link (scanned with the phone camera)."""
    try:
        import segno
    except ImportError:
        raise HTTPException(404, "QR codes need the segno package (pip install segno)")
    if not text or len(text) > 500:
        raise HTTPException(422, "bad text")
    import io
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0b1d2a", xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/portal/stats")
def portal_stats_api(db: Session = Depends(get_db), _=Depends(require("personnel.view"))):
    return portal_stats(db)


def portal_stats(db: Session) -> dict:
    active = db.scalar(select(func.count()).select_from(m.Employee).where(m.Employee.status == "active")) or 0
    enabled = db.scalar(select(func.count()).select_from(m.Employee).where(
        m.Employee.status == "active", m.Employee.portal_enabled.is_(True))) or 0
    used = db.scalar(select(func.count()).select_from(m.Employee).where(
        m.Employee.portal_enabled.is_(True), m.Employee.portal_last_login.is_not(None))) or 0
    pending = sum(db.scalar(select(func.count()).select_from(mod).where(mod.status == "pending")) or 0
                  for mod in (m.Leave, m.ManualLog, m.Overtime))
    return {"active": active, "enabled": enabled, "signed_in": used, "pending": pending,
            "on": bool(store.get(db, "portal.enabled"))}
