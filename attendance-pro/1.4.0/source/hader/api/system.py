"""Login, dashboard, reports, system settings, users/roles, audit, backup."""
from __future__ import annotations

import gzip
import os
import json
import sqlite3
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import store
from ..config import settings
from ..db import engine as db_engine, get_db, now
from .. import models as m
from ..engine import Engine
from .. import reports as R
from .. import backup as B
from ..security import (PERMISSIONS, LoginThrottle, hash_password, make_token,
                        user_permissions, validate_password, verify_password)
from ..version import APP_NAME, VERSION
from .crud import crud_router
from .deps import COOKIE, audit, current_user, ids_param, page, parse_date, require, ser
from .devices import is_online

router = APIRouter(prefix="/api")


class ExportFileResponse(FileResponse):
    """Delete sensitive temporary exports even on disconnects and rejected ranges."""
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

_LOGIN_THROTTLE = LoginThrottle()


def _user_dict(u: m.User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "is_superuser": u.is_superuser,
            "role": u.role.name if u.role else "", "permissions": sorted(user_permissions(u)),
            "language": u.language, "must_change_password": u.must_change_password,
            "email": u.email or "", "telegram_chat_id": u.telegram_chat_id or ""}


@router.post("/auth/login")
def login(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db)):
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    ip = request.client.host if request.client else ""
    account = username.casefold()
    if not _LOGIN_THROTTLE.check(account, ip):
        raise HTTPException(429, "too many failed attempts, try again in 10 minutes")
    if not username or len(username) > 100 or len(password) > 200:
        _LOGIN_THROTTLE.failure(account[:100], ip)
        raise HTTPException(401, "wrong username or password")
    user = db.scalar(select(m.User).where(func.lower(m.User.username) == username.lower()))
    if not user or not user.active or not verify_password(password, user.password_hash):
        _LOGIN_THROTTLE.failure(account, ip)
        db.add(m.AuditLog(username=username[:50], action="login_failed",
                          ip=request.client.host if request.client else ""))
        db.commit()
        raise HTTPException(401, "wrong username or password")
    _LOGIN_THROTTLE.success(account, ip)
    user.last_login = now()
    token = make_token(user.id, user.password_hash)
    from ..portal_gate import is_https
    response.set_cookie(COOKIE, token, httponly=True, secure=is_https(request), samesite="lax",
                        max_age=settings.session_hours * 3600, path="/")
    db.add(m.AuditLog(username=user.username, action="login", ip=request.client.host if request.client else ""))
    db.commit()
    return {"token": token, "user": _user_dict(user)}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(user: m.User = Depends(current_user)):
    return _user_dict(user)


@router.post("/auth/password")
def change_password(request: Request, response: Response, data: dict = Body(...), db: Session = Depends(get_db),
                    user: m.User = Depends(current_user)):
    if not verify_password(str(data.get("old_password", "")), user.password_hash):
        raise HTTPException(422, "current password is wrong")
    new = str(data.get("new_password", ""))
    try:
        validate_password(new, user.username, str(data.get("old_password", "")))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user.password_hash = hash_password(new)
    user.must_change_password = False
    audit(db, request, "password", "user", user.username)
    db.commit()
    if user.username == "admin":
        (settings.data_dir / "initial_admin_password.txt").unlink(missing_ok=True)
    token = make_token(user.id, user.password_hash)
    from ..portal_gate import is_https
    response.set_cookie(COOKIE, token, httponly=True, secure=is_https(request), samesite="lax",
                        max_age=settings.session_hours * 3600, path="/")
    return {"ok": True, "token": token}


@router.post("/auth/language")
def set_language(data: dict = Body(...), db: Session = Depends(get_db), user: m.User = Depends(current_user)):
    if data.get("language") in ("ar", "en"):
        user.language = data["language"]
        db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def _trend_row(d, rows) -> dict:
    expected = sum(1 for r in rows if r.status not in ("off", "holiday", "leave"))
    came = sum(1 for r in rows if r.clock_in or r.clock_out)
    return {"date": d.isoformat(), "present": came, "late": sum(1 for r in rows if r.late),
            "absent": sum(1 for r in rows if r.status == "absent"), "expected": expected,
            "rate": round(100 * came / expected, 1) if expected else None}


# Keep historical trends briefly, but invalidate them on committed attendance
# changes too: offline devices can upload old punches at any time.
_TREND_CACHE: dict = {}
TREND_TTL = 300


def _past_trend(db, first_day, last_day) -> list[dict]:
    import time as _t
    from .. import events
    key = (first_day, last_day, events.generation())
    hit = _TREND_CACHE.get(key)
    if hit and _t.monotonic() - hit[0] < TREND_TTL:
        return hit[1]
    rows = Engine(db, first_day, last_day, include_resigned=False).run()
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r.att_date, []).append(r)
    out = [_trend_row(first_day + timedelta(days=i), by_day.get(first_day + timedelta(days=i), []))
           for i in range((last_day - first_day).days + 1)]
    _TREND_CACHE.clear()
    _TREND_CACHE[key] = (_t.monotonic(), out)
    return out


def invalidate_trend() -> None:
    _TREND_CACHE.clear()


@router.get("/events")
async def live_events(request: Request, after: int = -1, once: bool = False, _=Depends(current_user)):
    """Server-Sent Events: pages listen here and refresh the moment something happens."""
    import asyncio
    import json as _json
    from fastapi.responses import StreamingResponse
    from .. import events

    async def stream():
        seq = events.current() if after < 0 else after
        yield f"retry: 3000\nevent: hello\ndata: {_json.dumps({'seq': seq})}\n\n"
        idle = 0.0
        while True:
            seq, items = events.since(seq)
            for s_, kind, data in items:
                yield f"id: {s_}\nevent: {kind}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"
            if once or await request.is_disconnected():
                return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                idle = 0
                yield ": keep-alive\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------
# Dashboard snapshot: kept ready and refreshed in the background, so opening the
# dashboard never waits for the attendance engine (3000 employees ~0.4 s).
# --------------------------------------------------------------------------
import threading as _threading
import time as _time

_SNAP: dict = {}            # lang -> (monotonic time, payload)
_SNAP_DIRTY = _threading.Event()
_SNAP_LOCK = _threading.Lock()
SNAP_MAX_AGE = 60           # seconds: the "not arrived yet" figures move with the clock


def mark_dashboard_dirty(*_a, **_k) -> None:
    _SNAP_DIRTY.set()


def _build_snapshot(lang: str) -> dict:
    from ..db import SessionLocal
    from ..i18n import LANG
    token = LANG.set(lang)
    try:
        with SessionLocal() as db:
            return compute_dashboard(db)
    finally:
        LANG.reset(token)


def refresh_dashboard(langs=("ar", "en")) -> None:
    """Rebuild the snapshot(s); only one rebuild runs at a time."""
    if not _SNAP_LOCK.acquire(blocking=False):
        return
    try:
        _SNAP_DIRTY.clear()
        for lang in langs:
            _SNAP[lang] = (_time.monotonic(), _build_snapshot(lang))
    finally:
        _SNAP_LOCK.release()


def _refresh_soon() -> None:
    _threading.Thread(target=refresh_dashboard, daemon=True, name="dashboard").start()


@router.get("/dashboard")
def dashboard(request: Request, _=Depends(current_user)):
    from ..i18n import lang
    lg = lang()
    hit = _SNAP.get(lg)
    if hit is None:
        refresh_dashboard((lg,))
        hit = _SNAP[lg]
    elif _SNAP_DIRTY.is_set() or _time.monotonic() - hit[0] > SNAP_MAX_AGE:
        _refresh_soon()   # answer now with the ready copy; the fresh one follows in a moment
    return hit[1] | {"age": round(_time.monotonic() - hit[0], 1)}


def compute_dashboard(db: Session) -> dict:
    today = now().date()
    devices = db.scalars(select(m.Device)).all()
    online = sum(1 for d in devices if d.enabled and is_online(d))
    busy = set(db.scalars(select(m.DeviceCommand.device_sn).where(
        m.DeviceCommand.status.in_(("pending", "sent"))).distinct()).all())
    employees = db.scalar(select(func.count()).select_from(m.Employee).where(m.Employee.status == "active")) or 0
    lo = datetime.combine(today, time.min)
    punches_today = db.scalar(select(func.count()).select_from(m.Transaction).where(
        m.Transaction.punch_time >= lo, m.Transaction.punch_time < lo + timedelta(days=1))) or 0
    days = Engine(db, today, today, include_resigned=False).run()
    counts: dict[str, int] = {}
    for r in days:
        counts[r.status] = counts.get(r.status, 0) + 1
    present = sum(counts.get(k, 0) for k in ("present", "late", "early", "late_early", "incomplete"))
    present += sum(1 for r in days if r.status == "unscheduled" and r.clock_in)
    # 14-day trend (yesterday's figures give the deltas on the tiles)
    span = 14
    first_day = today - timedelta(days=span - 1)
    trend = _past_trend(db, first_day, today - timedelta(days=1)) + [_trend_row(today, days)]
    # punches per hour today, and the average per hour over the previous 7 days
    hourly = [0] * 24
    for (t,) in db.execute(select(m.Transaction.punch_time).where(
            m.Transaction.punch_time >= lo, m.Transaction.punch_time < lo + timedelta(days=1))).all():
        hourly[t.hour] += 1
    avg = [0.0] * 24
    for (t,) in db.execute(select(m.Transaction.punch_time).where(
            m.Transaction.punch_time >= lo - timedelta(days=7), m.Transaction.punch_time < lo)).all():
        avg[t.hour] += 1 / 7
    depts: dict[str, dict] = {}
    for r in days:
        x = depts.setdefault(r.department or "-", {"department": r.department or "-", "total": 0, "present": 0,
                                                   "late": 0, "expected": 0})
        x["total"] += 1
        x["expected"] += 0 if r.status in ("off", "holiday", "leave") else 1
        x["present"] += 1 if (r.clock_in or r.clock_out) else 0
        x["late"] += 1 if r.late else 0
    for x in depts.values():
        x["rate"] = round(100 * x["present"] / x["expected"], 1) if x["expected"] else None
    pending = {
        "leaves": db.scalar(select(func.count()).select_from(m.Leave).where(m.Leave.status == "pending")) or 0,
        "manual": db.scalar(select(func.count()).select_from(m.ManualLog).where(m.ManualLog.status == "pending")) or 0,
        "overtime": db.scalar(select(func.count()).select_from(m.Overtime).where(m.Overtime.status == "pending")) or 0,
        "commands": db.scalar(select(func.count()).select_from(m.DeviceCommand).where(
            m.DeviceCommand.status.in_(("pending", "sent")))) or 0,
    }
    return {
        "date": today.isoformat(), "employees": employees, "devices": len(devices), "online": online,
        "offline": sum(1 for d in devices if d.enabled) - online, "punches_today": punches_today,
        "present": present, "late": counts.get("late", 0) + counts.get("late_early", 0),
        "absent": counts.get("absent", 0), "leave": counts.get("leave", 0), "off": counts.get("off", 0) + counts.get("holiday", 0),
        "incomplete": counts.get("incomplete", 0), "not_yet": counts.get("pending", 0),
        "expected": sum(1 for r in days if r.status not in ("off", "holiday", "leave")),
        "yesterday": trend[-2] if len(trend) > 1 else None,
        "trend": trend, "hourly": hourly, "hourly_avg": [round(v, 1) for v in avg],
        "departments": sorted(depts.values(), key=lambda x: (-(x["rate"] or 0), x["department"])),
        "device_list": [{"id": d.id, "sn": d.sn, "alias": d.label, "ip": d.ip, "area": d.area.label if d.area else "",
                         "state": "disabled" if not d.enabled else ("online" if is_online(d) else "offline"),
                         "last_activity": d.last_activity.strftime("%Y-%m-%d %H:%M:%S") if d.last_activity else "",
                         "users": d.user_count, "faces": d.face_count, "fps": d.fp_count, "palms": d.palm_count,
                         "records": d.att_count, "link": "4370" if d.managed_by == "tcp" else "ADMS",
                         "model": d.model,
                         "transferring": d.enabled and is_online(d) and d.sn in busy}
                        for d in devices],
        "pending": pending,
    }


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

@router.get("/reports")
def report_list(_=Depends(require("reports.view"))):
    return [{"key": k, "title_ar": v[0], "title_en": v[1], "kind": v[2]} for k, v in R.REPORTS.items()]


@router.get("/reports/{key}")
def report(request: Request, key: str, start: str = "", end: str = "", employee_ids: str = "", department_ids: str = "",
           device: str = "", lang: str = "ar", fmt: str = "json", q: str = "", status: str = "",
           offset: int = 0, limit: int = 200, db: Session = Depends(get_db),
           _=Depends(require("reports.view"))):
    if key not in R.REPORTS:
        raise HTTPException(404, "unknown report")
    today = now().date()
    ds = parse_date(start, today.replace(day=1))
    de = parse_date(end, today)
    if de < ds or (de - ds).days > 400:
        raise HTTPException(422, "invalid range (max 400 days)")
    off = max(0, offset)
    lim = max(1, min(limit, 1000))
    name = f"{key}_{ds}_{de}"
    if fmt in ("csv", "xlsx"):
        rep = R.build(db, key, ds, de, lang=lang, employee_ids=ids_param(employee_ids),
                      department_ids=ids_param(department_ids), device_sn=device or None,
                      q=q[:100], status=status[:40], limit=None, stream=True)
        fd, temporary = tempfile.mkstemp(prefix="hader_export_", suffix=f".{fmt}")
        os.close(fd)
        path = Path(temporary)
        try:
            if fmt == "csv":
                with path.open("wb") as dest:
                    for chunk in R.iter_csv(rep):
                        dest.write(chunk)
                media_type = "text/csv"
            else:
                company = store.get(db, "company.name_ar" if lang == "ar" else "company.name")
                R.to_xlsx(rep, company=company, rtl=(lang == "ar"), output=path)
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return ExportFileResponse(path, filename=f"{name}.{fmt}", media_type=media_type)
    rep = _report_cached(db, key, ds, de, lang, employee_ids, department_ids, device, off, lim, q, status)
    body = json.dumps(rep, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    if "gzip" in (request.headers.get("accept-encoding") or "") and len(body) > 4096:
        return Response(gzip.compress(body, 5), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json")


@router.get("/reports/{key}/print")
def print_report(key: str, start: str = "", end: str = "", employee_ids: str = "", department_ids: str = "",
                 device: str = "", lang: str = "ar", q: str = "", status: str = "", orientation: str = "auto",
                 db: Session = Depends(get_db), _=Depends(require("reports.view"))):
    from ..printing import write_document
    if key not in R.REPORTS:
        raise HTTPException(404, "unknown report")
    if orientation not in {"auto", "portrait", "landscape"} or lang not in {"ar", "en"}:
        raise HTTPException(422, "invalid print orientation or language")
    today = now().date()
    ds, de = parse_date(start, today.replace(day=1)), parse_date(end, today)
    if de < ds or (de - ds).days > 400 or de == date.max:
        raise HTTPException(422, "invalid range (max 400 days)")
    if db.bind.dialect.name == "sqlite":
        driver = db.connection().connection.driver_connection
        if not driver.in_transaction:
            driver.execute("BEGIN")
    rep = R.build(db, key, ds, de, lang=lang, employee_ids=ids_param(employee_ids),
                  department_ids=ids_param(department_ids), device_sn=device or None,
                  q=q[:100], status=status[:40], limit=None, stream=True)
    fd, temporary = tempfile.mkstemp(prefix="hader_print_", suffix=".html")
    os.close(fd)
    path = Path(temporary)
    try:
        with path.open("w", encoding="utf-8") as target:
            metadata = write_document(rep, target, company=store.get(db, "company.name_ar" if lang == "ar" else "company.name"),
                                      lang=lang, orientation=orientation)
        return ExportFileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store",
                                  "X-Print-Rows": str(metadata["rows"]), "X-Print-Orientation": metadata["orientation"]})
    except BaseException:
        path.unlink(missing_ok=True)
        raise


_REPORTS: "OrderedDict[tuple, dict]" = OrderedDict()
_REPORTS_LOCK = _threading.Lock()


def _report_cached(db, key, ds, de, lang, employee_ids, department_ids, device,
                   offset: int = 0, limit: int = 200, q: str = "", status: str = "") -> dict:
    """The last few reports are kept until data changes, so Show -> Excel -> Print compute once."""
    from .. import events
    ck = (key, ds, de, lang, employee_ids, department_ids, device or "", offset, limit, q[:100], status[:40])
    gen = events.generation()
    ttl = 20 if de >= now().date() - timedelta(days=1) else 600
    with _REPORTS_LOCK:
        hit = _REPORTS.get(ck)
        adjacent = 0 if key in ("transactions", "leave") else 1
        if hit and _time.monotonic() - hit["at"] < ttl and not events.affects_since(
                hit["gen"], (ds - timedelta(days=adjacent)).isoformat(),
                (de + timedelta(days=adjacent)).isoformat(), until=gen):
            _REPORTS.move_to_end(ck)
            hit["gen"] = gen
            return hit["rep"]
    rep = R.build(db, key, ds, de, lang=lang, employee_ids=ids_param(employee_ids),
                  department_ids=ids_param(department_ids), device_sn=device or None,
                  offset=offset, limit=limit, q=q[:100], status=status[:40])
    with _REPORTS_LOCK:
        _REPORTS[ck] = {"gen": gen, "at": _time.monotonic(), "rep": rep}
        while len(_REPORTS) > 6:
            _REPORTS.popitem(last=False)
    return rep


# --------------------------------------------------------------------------
# Settings, users, roles, audit, backup
# --------------------------------------------------------------------------

SECRET_MASK = "********"
SECRET_KEYS = ("alerts.smtp_password", "alerts.telegram_token")


@router.get("/ping")
def ping():
    """For the page to know the server is back after a restart (no sign-in needed)."""
    return {"ok": True, "version": VERSION}


@router.post("/system/control")
def control(request: Request, data: dict = Body(...)):
    """For the Service Manager on the server PC: restart (to apply new address/ports) or stop.
    Only from this PC, and only with the control token derived from the data folder's secret."""
    import hmac
    from .. import runtime
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1") or not hmac.compare_digest(
            request.headers.get("x-hader-control", ""), runtime.control_token()):
        raise HTTPException(403, "forbidden")
    action = data.get("action")
    if action == "restart":
        runtime.remember_previous()
        runtime.request_restart()
    elif action == "stop":
        runtime.request_stop()
    else:
        raise HTTPException(422, "unknown action")
    return {"ok": True, "action": action}


@router.get("/system/whoami")
def whoami(request: Request, _=Depends(current_user)):
    return {"ip": request.client.host if request.client else ""}


@router.get("/system/network")
def get_network(_=Depends(require("system.admin"))):
    from .. import portal_gate, runtime
    return runtime.current() | {"addresses": ["0.0.0.0", *portal_gate.lan_addresses(), "127.0.0.1"]}


@router.put("/system/network")
def put_network(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                _=Depends(require("system.admin"))):
    """Change the address and ports the server listens on; the server restarts on them."""
    import socket as _socket
    from .. import portal_gate, runtime
    from ..config import write_network
    host = str(data.get("host") or "0.0.0.0").strip()
    if host not in ("0.0.0.0", "127.0.0.1", *portal_gate.lan_addresses()):
        raise HTTPException(422, "هذا العنوان ليس من عناوين هذا الحاسوب / Not an address of this PC")
    try:
        web = int(data.get("web_port"))
        portal = int(data.get("portal_port") or 0)
        adms = [int(p) for p in str(data.get("adms_ports") or "").replace(";", ",").split(",") if p.strip()]
    except (TypeError, ValueError):
        raise HTTPException(422, "أرقام المنافذ غير صحيحة / Invalid port numbers")
    every = [web, *([portal] if portal else []), *adms]
    if any(not 1 <= p <= 65535 for p in every) or len(set(every)) != len(every):
        raise HTTPException(422, "كل منفذ يجب أن يكون بين 1 و65535 ومختلفاً عن غيره / Ports must be 1-65535 and all different")
    mine = {settings.web_port, settings.portal_port, *settings.adms_ports}
    busy = []
    for p in (web, *([portal] if portal else [])):   # device ports may be held by another server: taken when free
        if p in mine and host == settings.host:
            continue
        s_ = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        if os.name != "nt":   # same as the server's own bind (a port in TIME_WAIT is usable)
            s_.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            s_.bind((host, p))
        except OSError:
            if p not in mine:
                busy.append(p)
        finally:
            s_.close()
    if busy:
        raise HTTPException(409, f"المنفذ {busy[0]} مستخدم من برنامج آخر / Port {busy[0]} is used by another program")
    new = {"host": host, "web_port": web, "portal_port": portal, "adms_ports": ",".join(map(str, adms))}
    runtime.remember_previous()
    write_network(settings.data_dir, new)
    audit(db, request, "update", "network", str(new))
    db.commit()
    runtime.request_restart()
    return {"ok": True, "restarting": True} | new


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _=Depends(current_user)):
    s = store.all_(db)
    from ..policy_history import PolicyTimeline
    from ..timekeeping import zone_name
    from ..operability import synchronous_mode
    current_rules = PolicyTimeline(db).at("rules", 0, now().date())
    if current_rules:
        s.update(current_rules["snapshot"])
    s["time.zone"] = zone_name()
    s["storage.synchronous"] = synchronous_mode()
    for k in SECRET_KEYS:
        if s.get(k):
            s[k] = SECRET_MASK
    s["_server"] = {"app": APP_NAME, "version": VERSION, "web_port": settings.web_port,
                    "adms_ports": settings.adms_ports, "data_dir": str(settings.data_dir),
                    "server_time_utc": datetime.now(timezone.utc).isoformat(),
                    "utc_offset_minutes": round((now() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 60)}
    return s


@router.put("/settings")
def put_settings(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                 _=Depends(require("system.admin"))):
    from .. import portal_gate, timekeeping, operability
    from ..policy_history import validate_rule_changes
    data = dict(data)
    if rules := {k: v for k, v in data.items() if k.startswith("att.")}:
        try:
            validate_rule_changes(rules)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    for key, value in data.items():
        if isinstance(store.DEFAULTS.get(key), bool) and type(value) is not bool:
            raise HTTPException(422, f"{key} must be true or false")
    bounds = {"intake.retention_days": (1, 3650), "intake.max_batches": (1, 1000000),
              "intake.max_bytes": (1048576, 2147483648), "backup.keep": (1, 3650),
              "backup.hour": (0, 23), "tcp.poll_minutes": (1, 1440),
              "discovery.minutes": (1, 1440), "alerts.smtp_port": (1, 65535),
              "portal.correction_days": (0, 3650)}
    for key, (low, high) in bounds.items():
        if key in data and (type(data[key]) is not int or not low <= data[key] <= high):
            raise HTTPException(422, f"{key} must be an integer in {low}..{high}")
    try:
        for key, validator in (("time.zone", timekeeping.validate_zone),
                               ("storage.synchronous", operability.validate_synchronous),
                               ("backup.offsite_path", operability.validate_offsite_path)):
            if key in data:
                data[key] = validator(data[key])
        for key, env in (("time.zone", "HADER_TIMEZONE"), ("storage.synchronous", "HADER_STORAGE_SYNCHRONOUS")):
            if key in data and env in os.environ and data[key] != os.environ[env]:
                raise ValueError(f"{key} is configured by the server environment")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if "security.admin_from" in data:
        rule = str(data["security.admin_from"] or "").strip()
        data["security.admin_from"] = rule
        if not portal_gate.admin_allowed(request.client.host if request.client else "", rule):
            raise HTTPException(422, "هذا الإعداد سيمنع جهازك الحالي من الدخول؛ أضف عنوانه أولاً / "
                                     "This would lock out the device you are using; add its address first")
    enabling_writes = bool(data.get("tcp.write_back")) and not bool(store.get(db, "tcp.write_back"))
    db.info["policy_actor"] = getattr(request.state, "user", _).username
    before = {key: ("[hidden]" if key in SECRET_KEYS else store.get(db, key))
              for key in data if key in store.DEFAULTS}
    for k, v in data.items():
        if k in SECRET_KEYS and v == SECRET_MASK:
            continue  # unchanged secret
        if k in store.DEFAULTS:
            store.set_(db, k, v)
    if enabling_writes:
        # Persist the explicit opt-in before queuing, so all transports use the same
        # gate.  A transaction error rolls the setting and its queue changes back.
        from ..adms import sync
        for d in db.scalars(select(m.Device).where(m.Device.enabled.is_(True))).all():
            sync.sync_device(db, d)
    after = {key: ("[hidden]" if key in SECRET_KEYS else data[key]) for key in before}
    audit(db, request, "update", "settings", json.dumps({"before": before, "after": after}, ensure_ascii=False))
    saved_files = {}
    try:
        for key, filename, configure in (("time.zone", "timezone.ini", timekeeping.configure_zone),
                                          ("storage.synchronous", "storage.ini", operability.configure_synchronous)):
            if key in data:
                path = settings.data_dir / filename
                saved_files[path] = path.read_bytes() if path.exists() else None
                configure(data[key])
        db.commit()
    except Exception:
        db.rollback()
        for path, original in saved_files.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        timekeeping._CACHE.clear()
        raise
    portal_gate._ADMIN["at"] = 0.0   # apply the admin-access rule at once
    return get_settings(db, _)


def _user_out(db, u: m.User) -> dict:
    d = ser(u)
    d.pop("password_hash", None)
    d["role"] = u.role.name if u.role else ""
    return d


def _user_before(db, u: m.User, data: dict, is_new: bool) -> None:
    pw = data.get("password")
    if is_new and not pw:
        raise HTTPException(422, "password required")
    if pw:
        try:
            validate_password(str(pw), u.username)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        u.password_hash = hash_password(pw)
    if not u.username:
        raise HTTPException(422, "username required")


router.include_router(crud_router(m.User, "/users", "system.admin", "system.admin", search=("username", "full_name"),
                                  order=m.User.username, to_dict=_user_out, before_save=_user_before))


def _role_out(db, r: m.Role) -> dict:
    d = ser(r)
    d["permissions"] = json.loads(r.permissions or "[]")
    return d


def _role_before(db, r: m.Role, data: dict, is_new: bool) -> None:
    perms = data.get("permissions")
    if isinstance(perms, list):
        r.permissions = json.dumps([p for p in perms if p in PERMISSIONS])


router.include_router(crud_router(m.Role, "/roles", "system.admin", "system.admin", order=m.Role.name,
                                  to_dict=_role_out, before_save=_role_before))


@router.get("/permissions")
def permissions(_=Depends(current_user)):
    return PERMISSIONS


@router.get("/audit")
def audit_log(q: str = "", offset: int = 0, limit: int = 100, db: Session = Depends(get_db),
              _=Depends(require("system.admin"))):
    stmt = select(m.AuditLog)
    if q:
        stmt = stmt.where(m.AuditLog.username.ilike(f"%{q}%") | m.AuditLog.action.ilike(f"%{q}%")
                          | m.AuditLog.target.ilike(f"%{q}%"))
    total, rows = page(db, stmt.order_by(m.AuditLog.id.desc()), offset, limit)
    return {"total": total, "rows": [ser(r[0]) for r in rows]}


def make_backup(label: str = "manual") -> Path:
    """Create an integrity-checked ZIP containing DB, photos and configuration."""
    try:
        path = B.create_snapshot(label)
        from ..operability import after_backup
        after_backup(path)
        return path
    except B.BackupError as exc:
        raise HTTPException(422, str(exc)) from exc


def prune_backups(keep: int) -> None:
    files = sorted([*settings.backups_dir.glob("hader_*.zip"), *settings.backups_dir.glob("hader_*.db")])
    for f in files[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)


@router.get("/backups")
def list_backups(_=Depends(require("system.admin"))):
    files = sorted([*settings.backups_dir.glob("hader_*.zip"), *settings.backups_dir.glob("hader_*.db")], reverse=True)
    return {"rows": [{"name": f.name, "size": f.stat().st_size,
                      "time": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")} for f in files]}


@router.post("/backups")
def create_backup(request: Request, db: Session = Depends(get_db), _=Depends(require("system.admin"))):
    path = make_backup()
    prune_backups(int(store.get(db, "backup.keep") or 14))
    audit(db, request, "backup", "system", path.name)
    db.commit()
    return {"name": path.name}


@router.get("/backups/{name}")
def download_backup(name: str, _=Depends(require("system.admin"))):
    path = settings.backups_dir / Path(name).name
    if not path.exists() or not path.name.startswith("hader_") or path.suffix not in (".zip", ".db"):
        raise HTTPException(404, "not found")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.post("/backups/{name}/restore")
def restore_backup(name: str, request: Request, db: Session = Depends(get_db),
                   _=Depends(require("system.admin"))):
    path = settings.backups_dir / Path(name).name
    if not path.exists() or not path.name.startswith("hader_") or path.suffix not in (".zip", ".db"):
        raise HTTPException(404, "not found")
    try:
        result = B.schedule_restore(path, getattr(request.state, "user", None).username if getattr(request.state, "user", None) else "admin")
    except B.BackupError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, request, "restore.scheduled", "system", path.name)
    db.commit()
    from .. import runtime
    runtime.request_restart()
    return result | {"restarting": True}


@router.get("/about")
def about():
    return {"app": APP_NAME, "version": VERSION}
