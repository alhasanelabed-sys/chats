"""Admin-managed demo sessions and local, non-network synthetic event generation."""
from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db, now
from ..demo_environments import LIMITS, SESSION_SECONDS, get_manager, validate_options
from ..demo_guard import _ACCESS_THROTTLE, is_demo
from ..experiments import ExperimentError
from .deps import COOKIE, audit, require

router = APIRouter(tags=["demo"])
_SIMULATE_LOCK = threading.Lock()
_ACCESS_LOCK = threading.Lock()


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ExperimentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def _job(job, request):
    token = job.pop("access_token", "")
    if job.get("status") == "running" and token and job.get("port"):
        hostname = request.url.hostname or "127.0.0.1"
        if ":" in hostname:
            hostname = f"[{hostname}]"
        job["open_url"] = f"http://{hostname}:{job['port']}/#demo-access={token}"
    return job


@router.get("/api/demo-environments", dependencies=[Depends(require("system.admin"))])
def list_environments(request: Request):
    return {"jobs": [_job(job, request) for job in get_manager().list()], "limits": LIMITS}


@router.post("/api/demo-environments", status_code=202, dependencies=[Depends(require("system.admin"))])
def start_environment(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    try:
        config = validate_options(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    def accepted():
        audit(db, request, "demo.start", "demo", f"employees={config['employees']}, devices={config['devices']}, days={config['days']}")
        db.commit()

    return _job(_call(get_manager().start, config, on_accept=accepted), request)


@router.get("/api/demo-environments/{job_id}", dependencies=[Depends(require("system.admin"))])
def get_environment(job_id: str, request: Request):
    return _job(_call(get_manager().get, job_id), request)


@router.post("/api/demo-environments/{job_id}/stop", dependencies=[Depends(require("system.admin"))])
def stop_environment(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = _call(get_manager().stop, job_id)
    audit(db, request, "demo.stop", "demo", job_id)
    db.commit()
    return _job(job, request)


@router.delete("/api/demo-environments/{job_id}", dependencies=[Depends(require("system.admin"))])
def delete_environment(job_id: str, request: Request, db: Session = Depends(get_db)):
    _call(get_manager().delete, job_id)
    audit(db, request, "demo.delete", "demo", job_id)
    db.commit()
    return {"ok": True}


@router.get("/api/demo/info")
def demo_info():
    if not is_demo():
        return {"is_demo": False}
    return {"is_demo": True, "expires_at": os.environ.get("HADER_DEMO_EXPIRES", ""),
            **{key: int(os.environ.get(f"HADER_DEMO_{key.upper()}", "0")) for key in ("employees", "devices", "days")},
            "isolated": True, "device_access": False, "physical_devices_verified": False,
            "server_local_time": now().isoformat()}


def _check_demo():
    if not is_demo():
        raise HTTPException(404, "not found")
    expires = os.environ.get("HADER_DEMO_EXPIRES", "")
    try:
        expired = datetime.fromisoformat(expires) <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        expired = True
    if expired:
        raise HTTPException(410, "انتهت مدة التجربة / Demo session expired")


@router.post("/api/demo/access")
def demo_access(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db)):
    with _ACCESS_LOCK:
        return _grant_demo_access(request, response, data, db)


def _grant_demo_access(request: Request, response: Response, data: dict, db: Session):
    _check_demo()
    peer = request.client.host if request.client else ""
    if not _ACCESS_THROTTLE.check("demo", peer):
        raise HTTPException(429, "too many failed access attempts")
    supplied = data.get("token")
    expected = os.environ.get("HADER_DEMO_ACCESS_TOKEN", "")
    if not isinstance(supplied, str) or not expected or len(supplied) > 128 or not secrets.compare_digest(supplied, expected):
        _ACCESS_THROTTLE.failure("demo", peer)
        raise HTTPException(401, "invalid demo access token")
    user = db.scalar(select(m.User).where(m.User.username == "admin", m.User.active.is_(True)))
    if user is None:
        raise HTTPException(503, "demo account unavailable")
    from ..security import make_token
    from ..portal_gate import is_https
    from .system import _user_dict
    _ACCESS_THROTTLE.success("demo", peer)
    token = make_token(user.id, user.password_hash, hours=2)
    response.set_cookie(COOKIE, token, httponly=True, secure=is_https(request), samesite="lax", max_age=SESSION_SECONDS, path="/")
    user.last_login = now()
    audit(db, request, "demo.access", "demo")
    db.commit()
    os.environ.pop("HADER_DEMO_ACCESS_TOKEN", None)
    return {"user": _user_dict(user)}


@router.post("/api/demo/simulate", dependencies=[Depends(require("device.control"))])
def simulate(request: Request, data: dict = Body(default={}), db: Session = Depends(get_db)):
    _check_demo()
    count = data.get("count", 1)
    if set(data) - {"count"} or type(count) is not int or not 1 <= count <= 100:
        raise HTTPException(422, "count: 1–100")
    with _SIMULATE_LOCK:
        employees = db.scalars(select(m.Employee).where(m.Employee.status == "active").order_by(m.Employee.id).limit(count)).all()
        devices = db.scalars(select(m.Device).where(m.Device.sn.like("SCALE%"), m.Device.ip == "", m.Device.tcp_poll.is_(False)).order_by(m.Device.id)).all()
        if not employees or not devices:
            raise HTTPException(409, "لا توجد بيانات تجريبية صالحة / No eligible synthetic data")
        before = db.scalar(select(func.count()).select_from(m.Transaction)) or 0
        if before + count > LIMITS["punches"]:
            raise HTTPException(409, "وصلت البيئة إلى الحد الأقصى للحركات / Demo punch limit reached")
        current = now()
        created = 0
        for index in range(count):
            employee = employees[index % len(employees)]
            device = devices[index % len(devices)]
            stamp = current - timedelta(seconds=index)
            while db.scalar(select(m.Transaction.id).where(m.Transaction.emp_code == employee.emp_code, m.Transaction.device_sn == device.sn, m.Transaction.punch_time == stamp)):
                stamp -= timedelta(seconds=count)
            if stamp.date() != current.date():
                continue
            latest = db.scalar(select(m.Transaction).where(m.Transaction.employee_id == employee.id, m.Transaction.punch_time < stamp).order_by(m.Transaction.punch_time.desc()).limit(1))
            state = 1 if latest and latest.punch_state == 0 else 0
            db.add(m.Transaction(employee_id=employee.id, emp_code=employee.emp_code, punch_time=stamp, punch_state=state,
                                 verify_type=15, device_sn=device.sn, source="demo"))
            device.last_activity = current
            device.last_sync = current
            device.att_count += 1
            db.add(m.DeviceOpLog(device_sn=device.sn, op_code=0, admin="demo", op_time=stamp,
                                obj1="محاكاة استلام حضور", obj2=employee.emp_code))
            db.flush()
            created += 1
        audit(db, request, "demo.simulate", "demo", f"created={created}; synthetic device → isolated server")
        db.commit()
        from ..events import publish
        for device in devices:
            publish("device", sn=device.sn, state="online")
        publish("punch", sn="demo", count=created)
        return {"created": created, "count": created, "today": current.date().isoformat(),
                "transactions_total": before + created, "device_sn": devices[0].sn}
