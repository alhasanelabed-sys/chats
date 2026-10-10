"""Device module: terminals, remote control, transactions and device logs."""
from __future__ import annotations

import json
import ipaddress
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..adms import commands as C
from ..adms import sync
from ..adms.protocol import VERIFY_TYPES
from ..adms.server import TRAFFIC
from .. import store
from ..config import settings
from ..db import get_db, now
from .. import models as m
from ..photos import with_photo
from .crud import coerce
from .deps import audit, page, parse_date, parse_dt, require, ser

router = APIRouter(prefix="/api")


def _writing(db) -> bool:
    from ..tcp_pull import writing_enabled
    return writing_enabled(db)


# Actions that change a terminal: refused in read-only mode (another program holds port 90).
WRITE_ACTIONS = {"sync_all", "sync_time", "reboot", "clear_log", "clear_photo", "clear_data", "enroll",
                 "delete_user", "custom", "check", "reupload_all", "upload_users"}


def is_online(d: m.Device) -> bool:
    limit = max(settings.offline_after, d.heartbeat * 3)
    if d.managed_by == "tcp":
        limit = max(limit, 12 * 60)  # direct-mode terminals are read every few minutes
    return bool(d.last_activity and (now() - d.last_activity).total_seconds() <= limit)


def dev_dict(db: Session, d: m.Device) -> dict:
    out = ser(d)
    out["area"] = d.area.label if d.area else ""
    out["label"] = d.label
    out["state"] = "disabled" if not d.enabled else ("online" if is_online(d) else "offline")
    out["pending"] = db.scalar(select(func.count()).select_from(m.DeviceCommand).where(
        m.DeviceCommand.device_sn == d.sn, m.DeviceCommand.status.in_(("pending", "sent")))) or 0
    if d.managed_by == "tcp" and not _writing(db):
        out["pending"] = 0  # read-only link (another server still runs the terminal): nothing is sent to it
    # green "transferring" arrows: online with commands still to deliver
    out["transferring"] = out["state"] == "online" and out["pending"] > 0
    # the terminal uploaded something in the last 20 s (punches, users, templates)
    out["receiving"] = bool(out["state"] == "online" and d.last_sync and (now() - d.last_sync).total_seconds() <= 20)
    try:
        out["options"] = json.loads(d.options or "{}")
    except json.JSONDecodeError:
        out["options"] = {}
    out["bio_support"] = {str(k): v for k, v in sync.device_bio_support(d).items()}
    out.pop("comm_key", None)
    return out


@router.get("/devices")
def list_devices(q: str = "", area_id: int | None = None, db: Session = Depends(get_db),
                 _=Depends(require("device.view"))):
    stmt = select(m.Device)
    if q:
        stmt = stmt.where(or_(m.Device.sn.ilike(f"%{q}%"), m.Device.alias.ilike(f"%{q}%"), m.Device.ip.ilike(f"%{q}%")))
    if area_id:
        stmt = stmt.where(m.Device.area_id == area_id)
    rows = db.scalars(stmt.order_by(m.Device.alias, m.Device.sn)).all()
    return {"total": len(rows), "rows": [dev_dict(db, d) for d in rows]}


@router.get("/devices/{dev_id}")
def get_device(dev_id: int, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    return dev_dict(db, d)


@router.post("/devices")
def add_device(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
               _=Depends(require("device.control"))):
    """Pre-register a terminal by serial number (it appears online once it connects)."""
    sn = (data.get("sn") or "").strip()
    if not sn:
        raise HTTPException(422, "serial number required")
    if db.scalar(select(m.Device.id).where(m.Device.sn == sn)):
        raise HTTPException(409, "device already exists")
    vals = coerce(m.Device, {k: v for k, v in data.items() if k in _EDITABLE})
    d = m.Device(sn=sn, **vals)
    _set_adms_acl(d, data.get("adms_allowed_ips"))
    d.alias = d.alias or sn
    db.add(d)
    db.flush()
    sync.sync_device(db, d)
    audit(db, request, "create", "device", sn)
    db.commit()
    return dev_dict(db, d)


_EDITABLE = {"alias", "area_id", "enabled", "is_attendance", "is_registration", "time_zone", "heartbeat",
             "trans_interval", "trans_times", "realtime", "comm_key", "tcp_port", "ip", "tcp_poll",
             "alias_en"}


def _set_adms_acl(device: m.Device, value) -> None:
    if value is None:
        return
    values = value if isinstance(value, list) else str(value).split(",")
    clean = []
    for item in values:
        try:
            clean.append(str(ipaddress.ip_network(str(item).strip(), strict=False)))
        except ValueError as exc:
            raise HTTPException(422, "invalid ADMS allowed IP or network") from exc
    try:
        opts = json.loads(device.options or "{}")
    except json.JSONDecodeError:
        opts = {}
    opts["adms_allowed_ips"] = clean
    device.options = json.dumps(opts, ensure_ascii=False, separators=(",", ":"))


@router.put("/devices/{dev_id}")
def update_device(dev_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                  _=Depends(require("device.control"))):
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    old_area = d.area_id
    _set_adms_acl(d, data.get("adms_allowed_ips"))
    for k, v in coerce(m.Device, {k: v for k, v in data.items() if k in _EDITABLE}).items():
        setattr(d, k, v)
    db.flush()
    n = 0
    if d.area_id != old_area and d.area_id:
        # Moving a terminal to another area replaces its people.
        n = sync.sync_device(db, d)
    if {"time_zone", "heartbeat", "trans_interval", "trans_times", "realtime"} & set(data):
        sync.queue(db, d.sn, C.SIMPLE["check"], "Reload options")
    audit(db, request, "update", "device", d.sn)
    db.commit()
    out = dev_dict(db, d)
    out["queued"] = n
    return out


@router.delete("/devices/{dev_id}")
def delete_device(dev_id: int, request: Request, db: Session = Depends(get_db),
                  _=Depends(require("device.control"))):
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    db.execute(delete(m.DeviceCommand).where(m.DeviceCommand.device_sn == d.sn))
    audit(db, request, "delete", "device", d.sn)
    db.delete(d)
    db.commit()
    return {"ok": True}


ACTIONS = {
    "sync_all": "مزامنة كل البيانات إلى الجهاز",
    "upload_users": "سحب المستخدمين والبصمات من الجهاز",
    "pull_bio": "سحب البصمات والوجوه والكف وصور الوجه من الجهاز",
    "upload_att": "سحب سجل الحضور من الجهاز",
    "reupload_all": "إعادة رفع كل البيانات من الجهاز",
    "sync_time": "مزامنة الوقت",
    "info": "قراءة معلومات الجهاز",
    "check": "إعادة تحميل الإعدادات",
    "reboot": "إعادة تشغيل",
    "clear_log": "حذف سجلات الحضور من الجهاز",
    "clear_photo": "حذف صور الحضور من الجهاز",
    "clear_data": "حذف كل البيانات من الجهاز",
    "enroll": "تسجيل بصمة عن بعد",
    "delete_user": "حذف مستخدم من الجهاز",
    "custom": "أمر مخصص",
}


@router.post("/devices/{dev_id}/action")
def device_action(dev_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require("device.control"))):
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    act = data.get("action")
    if act not in ACTIONS:
        raise HTTPException(422, "unknown action")
    if act in WRITE_ACTIONS and not _writing(db):
        raise HTTPException(409, "وضع القراءة فقط: المنفذ 90 مشغول ببرنامج آخر. أوقفه ليعمل هذا الأمر. / "
                                 "Read-only mode: port 90 is held by another program.")
    n = 0

    def q(content, title=""):
        nonlocal n
        if sync.queue(db, d.sn, content, title):
            n += 1

    if act == "sync_all":
        n = sync.sync_device(db, d)
    elif act == "upload_users":
        q(C.query_table("user"), "Upload users")
        q(C.query_table("biodata"), "Upload templates")
        d.op_stamp = "0"
        q(C.SIMPLE["check"], "Reload options")
    elif act == "pull_bio":
        if d.managed_by != "tcp":
            n = sync.pull_bio(db, d)
    elif act == "upload_att":
        start = parse_dt(data.get("start")) or datetime.combine(now().date() - timedelta(days=31), time.min)
        end = parse_dt(data.get("end")) or now()
        q(C.query_attlog(start, end), "Upload transactions")
    elif act == "reupload_all":
        sync.reset_stamps(d)
        q(C.SIMPLE["check"], "Reload options")
    elif act == "sync_time":
        q(C.set_time(now()), "Sync time")
    elif act in ("info", "check", "reboot", "clear_log", "clear_photo", "clear_data"):
        if act == "clear_data" and not user.is_superuser:
            raise HTTPException(403, "only a super administrator can wipe a device")
        q(C.SIMPLE[act], ACTIONS[act])
    elif act == "enroll":
        pin = str(data.get("emp_code", "")).strip()
        if not pin:
            raise HTTPException(422, "employee required")
        q(C.enroll_bio(pin, int(data.get("bio_type", 9)), int(data.get("finger", 0)), not sync._uses_biodata(d)),
          "Remote enroll " + pin)
    elif act == "delete_user":
        pin = str(data.get("emp_code", "")).strip()
        q(C.user_delete(pin), "Delete user " + pin)
    elif act == "custom":
        if not user.is_superuser:
            raise HTTPException(403, "only a super administrator can send raw commands")
        content = str(data.get("command", "")).strip()
        if not content or "\n" in content:
            raise HTTPException(422, "one command line required")
        q(content, "Custom")
    audit(db, request, "device." + act, "device", d.sn)
    db.commit()
    out = {"ok": True, "queued": n}
    if d.managed_by == "tcp" and d.ip:
        # Direct-mode terminal: run the commands now over 4370 instead of waiting for it to poll.
        from .. import tcp_pull as T
        try:
            if _writing(db):
                out["delivered"] = T.deliver_pending(d.sn)
            if act in ("upload_users", "upload_att", "reupload_all", "info", "pull_bio"):
                out["read"] = T.read_and_store(d.sn)
        except T.TcpPullError as exc:
            raise HTTPException(502, str(exc))
    return out


@router.post("/devices/pull-everyone")
def pull_everyone(request: Request, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """Ask every terminal for all its people: names, cards, fingerprints, faces, palms and
    photos. Push terminals answer at their next connection; direct (4370) ones are read now
    in the background."""
    import threading
    from .. import tcp_pull as T
    push, direct = 0, []
    for d in db.scalars(select(m.Device).where(m.Device.enabled.is_(True))).all():
        if d.managed_by == "tcp":
            if d.ip:
                direct.append(d.sn)
        elif sync.pull_bio(db, d):
            push += 1
    audit(db, request, "device.pull_everyone", "device", f"push {push}, direct {len(direct)}")
    db.commit()

    def read_all():
        for sn in direct:
            try:
                T.read_and_store(sn, with_bio=True)
            except Exception:  # noqa: BLE001 - one silent terminal must not stop the others
                pass
    if direct:
        threading.Thread(target=read_all, daemon=True, name="pull-everyone").start()
    return {"push": push, "direct": len(direct), "missing": missing_people(db)}


def missing_people(db: Session) -> dict:
    """People who punched but still lack a name, a picture or any biometric."""
    from ..photos import with_photo
    ids = set(db.scalars(select(m.Transaction.employee_id).where(m.Transaction.employee_id.is_not(None))
                         .distinct()).all())
    if not ids:
        return {"punched": 0, "no_name": 0, "no_photo": 0, "no_bio": 0}
    emps = db.execute(select(m.Employee.id, m.Employee.first_name, m.Employee.last_name)
                      .where(m.Employee.id.in_(ids))).all()
    pics = with_photo(db, ids)
    bio = set(db.scalars(select(m.BioTemplate.employee_id).where(m.BioTemplate.employee_id.in_(ids)).distinct()).all())
    return {"punched": len(emps), "no_name": sum(1 for _i, f, l in emps if not f"{f or ''}{l or ''}".strip()),
            "no_photo": sum(1 for i, *_x in emps if i not in pics), "no_bio": sum(1 for i, *_x in emps if i not in bio)}


@router.get("/device-missing-people")
def missing_people_api(db: Session = Depends(get_db), _=Depends(require("device.view"))):
    return missing_people(db)


@router.get("/device-commands")
def list_commands(sn: str = "", status: str = "", offset: int = 0, limit: int = 100,
                  db: Session = Depends(get_db), _=Depends(require("device.view"))):
    stmt = select(m.DeviceCommand)
    if sn:
        stmt = stmt.where(m.DeviceCommand.device_sn == sn)
    if status:
        stmt = stmt.where(m.DeviceCommand.status == status)
    total, rows = page(db, stmt.order_by(m.DeviceCommand.id.desc()), offset, limit)
    out = []
    for (c,) in rows:
        d = ser(c)
        d["content"] = c.content if len(c.content) < 200 else c.content[:200] + "…"
        out.append(d)
    return {"total": total, "rows": out}


@router.post("/device-commands/clear")
def clear_commands(request: Request, data: dict = Body(default={}), db: Session = Depends(get_db),
                   _=Depends(require("device.control"))):
    stmt = delete(m.DeviceCommand).where(m.DeviceCommand.status.in_(data.get("status") or ["done", "failed"]))
    if data.get("sn"):
        stmt = stmt.where(m.DeviceCommand.device_sn == data["sn"])
    res = db.execute(stmt)
    audit(db, request, "clear", "device_command", str(res.rowcount))
    db.commit()
    return {"deleted": res.rowcount}


@router.get("/device-traffic")
def device_traffic(sn: str = "", _=Depends(require("device.view"))):
    rows = [t for t in reversed(TRAFFIC) if not sn or t["sn"] == sn]
    return {"rows": rows[:300]}


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

def tx_dict(t: m.Transaction, e: m.Employee | None, aliases: dict) -> dict:
    d = ser(t)
    d["name"] = e.display_name if e else ""
    d["department"] = e.department.label if e and e.department else ""
    d["device"] = aliases.get(t.device_sn, t.device_sn)
    d["verify"] = VERIFY_TYPES.get(t.verify_type, "other")
    d["has_photo"] = bool(t.device_sn) and sync.photo_path_for(t.device_sn, t.emp_code, t.punch_time).exists()
    return d


@router.get("/transactions")
def list_transactions(q: str = "", start: str = "", end: str = "", sn: str = "", department_id: str = "",
                      offset: int = 0, limit: int = 50, db: Session = Depends(get_db),
                      _=Depends(require("attendance.view"))):
    stmt = select(m.Transaction, m.Employee).outerjoin(m.Employee, m.Employee.id == m.Transaction.employee_id)
    if q:
        stmt = stmt.where(or_(m.Transaction.emp_code.ilike(f"%{q}%"), m.Employee.first_name.ilike(f"%{q}%"),
                              m.Employee.last_name.ilike(f"%{q}%")))
    ds, de = parse_date(start), parse_date(end)
    if ds:
        stmt = stmt.where(m.Transaction.punch_time >= datetime.combine(ds, time.min))
    if de:
        stmt = stmt.where(m.Transaction.punch_time < datetime.combine(de + timedelta(days=1), time.min))
    if sn:
        stmt = stmt.where(m.Transaction.device_sn == sn)
    if department_id:
        stmt = stmt.where(m.Employee.department_id.in_([int(x) for x in department_id.split(",")]))
    total, rows = page(db, stmt.order_by(m.Transaction.punch_time.desc(), m.Transaction.id.desc()), offset, limit)
    aliases = {d.sn: d.label for d in db.scalars(select(m.Device)).all()}
    return {"total": total, "rows": [tx_dict(t, e, aliases) for t, e in rows]}


@router.get("/transactions/{tx_id}/photo")
def transaction_photo(tx_id: int, db: Session = Depends(get_db), _=Depends(require("attendance.view"))):
    t = db.get(m.Transaction, tx_id)
    if not t:
        raise HTTPException(404, "not found")
    p = sync.photo_path_for(t.device_sn, t.emp_code, t.punch_time)
    if not p.exists():
        raise HTTPException(404, "no photo")
    return FileResponse(p, media_type="image/jpeg")


@router.get("/monitor")
def realtime_monitor(after_id: int = 0, offset: int = 0, limit: int = 100, q: str = "", device: str = "",
                     db: Session = Depends(get_db), _=Depends(require("attendance.view"))):
    """All recorded movements for the institution's current calendar day.

    This uses the same Transaction rows and day boundaries as the register.
    Attendance calculation subsequently assigns these movements to shifts.
    """
    day = now().date()
    lower = datetime.combine(day, time.min)
    stmt = select(m.Transaction, m.Employee).outerjoin(m.Employee, m.Employee.id == m.Transaction.employee_id)
    stmt = stmt.where(m.Transaction.punch_time >= lower, m.Transaction.punch_time < lower + timedelta(days=1))
    if q.strip():
        pattern = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(m.Transaction.emp_code.ilike(pattern), m.Employee.first_name.ilike(pattern),
                              m.Employee.last_name.ilike(pattern),
                              (m.Employee.first_name + " " + m.Employee.last_name).ilike(pattern)))
    if device:
        stmt = stmt.where(m.Transaction.device_sn == device[:50])
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    current_max = db.scalar(select(func.max(m.Transaction.id)).where(
        m.Transaction.punch_time >= lower, m.Transaction.punch_time < lower + timedelta(days=1))) or 0
    off, lim = max(0, offset), max(1, min(limit, 1000))
    after_id = max(0, after_id)
    remaining = total
    if after_id:
        stmt = stmt.where(m.Transaction.id > after_id)
        remaining = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        stmt = stmt.order_by(m.Transaction.id).limit(lim)
    else:
        stmt = stmt.order_by(m.Transaction.punch_time.desc(), m.Transaction.id.desc()).offset(off).limit(lim)
    rows = db.execute(stmt).all()
    aliases = {d.sn: d.label for d in db.scalars(select(m.Device)).all()}
    pics = with_photo(db, [e.id for _t, e in rows if e])
    out = [tx_dict(t, e, aliases) | {"employee_has_photo": bool(e and e.id in pics)} for t, e in rows]
    last_id = max((row["id"] for row in out), default=max(after_id, current_max)) if after_id else current_max
    return {"date": day.isoformat(), "total": total, "rows": out, "last_id": last_id,
            "offset": off, "limit": lim, "has_more": len(out) < remaining if after_id else off + len(out) < total,
            "kind": "raw_movements"}


@router.get("/device-oplogs")
def list_oplogs(sn: str = "", offset: int = 0, limit: int = 100, db: Session = Depends(get_db),
                _=Depends(require("device.view"))):
    stmt = select(m.DeviceOpLog)
    if sn:
        stmt = stmt.where(m.DeviceOpLog.device_sn == sn)
    total, rows = page(db, stmt.order_by(m.DeviceOpLog.id.desc()), offset, limit)
    return {"total": total, "rows": [ser(r[0]) for r in rows]}


@router.get("/device-errorlogs")
def list_errorlogs(sn: str = "", offset: int = 0, limit: int = 100, db: Session = Depends(get_db),
                   _=Depends(require("device.view"))):
    stmt = select(m.DeviceErrorLog)
    if sn:
        stmt = stmt.where(m.DeviceErrorLog.device_sn == sn)
    total, rows = page(db, stmt.order_by(m.DeviceErrorLog.id.desc()), offset, limit)
    return {"total": total, "rows": [ser(r[0]) for r in rows]}


def _direct(dev_id: int, db: Session) -> m.Device:
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    if not d.ip:
        raise HTTPException(422, "device IP address is not set")
    return d


@router.post("/devices/{dev_id}/pull")
def tcp_pull(dev_id: int, request: Request, db: Session = Depends(get_db), _=Depends(require("device.control"))):
    """Read users, fingerprints, faces and punches straight from the terminal over 4370."""
    from .. import tcp_pull as T
    d = _direct(dev_id, db)
    try:
        result = T.read_and_store(d.sn)
    except T.TcpPullError as exc:
        raise HTTPException(502, str(exc))
    audit(db, request, "device.tcp_pull", "device", f"{d.sn}: {result['new']} new")
    db.commit()
    return result


@router.get("/tcp-status")
def tcp_status(_=Depends(require("device.view"))):
    from .. import tcp_pull as T
    return T.STATUS


@router.get("/devices/{dev_id}/server")
def device_server(dev_id: int, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """Where the terminal pushes now (its Cloud Server Setting), read over 4370."""
    from .. import tcp_pull as T
    d = _direct(dev_id, db)
    try:
        current = T.device_server_settings(d.sn)
    except T.TcpPullError as exc:
        raise HTTPException(502, str(exc))
    return {"current": current, "suggested_ip": T.local_ip_for(d.ip),
            "suggested_port": settings.adms_ports[0] if settings.adms_ports else settings.web_port}


@router.post("/devices/{dev_id}/server")
def set_device_server(dev_id: int, request: Request, data: dict = Body(default={}), db: Session = Depends(get_db),
                      _=Depends(require("device.control"))):
    """Make the terminal push (ADMS) to this program: faces, palms and photos then travel too."""
    from .. import tcp_pull as T
    d = _direct(dev_id, db)
    if not _writing(db):
        raise HTTPException(409, "وضع القراءة فقط: فعّل الكتابة على الأجهزة أولاً / Enable device writes first")
    try:
        result = T.point_to_this_server(d.sn, str(data.get("ip") or ""), int(data.get("port") or 0),
                                        bool(data.get("reboot", True)))
    except T.TcpPullError as exc:
        raise HTTPException(502, str(exc))
    audit(db, request, "device.point_to_server", "device", f"{d.sn} -> {result['server']}")
    db.commit()
    return result


@router.post("/devices/discover")
def discover_devices(request: Request, data: dict = Body(default={}), db: Session = Depends(get_db),
                     _=Depends(require("device.control"))):
    """Search the network for terminals and register them (no need to add them by hand)."""
    from .. import tcp_pull as T
    nets = data.get("networks")
    try:
        result = T.discover(str(nets) if nets else None, int(data.get("port") or 4370))
    except T.TcpPullError as exc:
        raise HTTPException(422, str(exc))
    audit(db, request, "device.discover", "device", f"{len(result['found'])} found")
    db.commit()
    return result


@router.get("/device-discovery")
def discovery_status(_=Depends(require("device.view"))):
    from .. import tcp_pull as T
    from ..terminal import discovery
    return {**T.DISCOVERY, "local": discovery.local_ipv4(), "default_networks": discovery.default_networks()}


@router.post("/devices/probe")
def probe_device(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                 _=Depends(require("device.control"))):
    """Add a terminal by its IP: connect, read its serial number and register it."""
    from .. import tcp_pull as T
    ip = str(data.get("ip") or "").strip()
    if not ip:
        raise HTTPException(422, "IP address required")
    port = int(data.get("port") or 4370)
    try:
        info, key = T.identify(ip, port, [str(data.get("comm_key") or "0")])
        sn, created = T.register_found(ip, info, key, port)
        read = T.read_and_store(sn)
    except T.TcpPullError as exc:
        raise HTTPException(502, str(exc))
    audit(db, request, "device.probe", "device", f"{sn} {ip}")
    db.commit()
    return {"sn": sn, "new": created, "info": info, "read": read}


@router.get("/ports")
def port_status(_=Depends(require("device.view"))):
    from ..adms import ports
    return ports.snapshot()


@router.get("/link-mode")
def link_mode(db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """full = this program owns the device port (90); read = another program holds it."""
    from ..adms import ports
    p = ports.device_port()
    info = ports.PORTS.get(p, {"mode": "none", "detail": ""})
    return {"full": ports.full_mode(), "writing": _writing(db), "port": p, "mode": info["mode"],
            "detail": info.get("detail", "")}


@router.post("/link-mode")
def set_link_mode(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                  _=Depends(require("system.admin"))):
    """release: give port 90 back (so another program can use it); take: use it again when free."""
    from ..adms import ports
    p = ports.device_port()
    act = data.get("action")
    if act == "release":
        ports.release(p)
    elif act == "take":
        ports.take(p)
    else:
        raise HTTPException(422, "action must be release or take")
    audit(db, request, "port." + act, "system", str(p))
    db.commit()
    return {"ok": True, "port": p}


# --------------------------------------------------------------------------
# Remote enrollment  and per-employee sync state
# --------------------------------------------------------------------------

ENROLL_TYPES = {1: "Fingerprint", 2: "Face (IR)", 8: "Palm", 9: "Visible face"}


@router.post("/employees/{emp_id}/enroll")
def remote_enroll(emp_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                  _=Depends(require("device.control"))):
    """Ask a terminal to enroll a finger / face / palm for this employee. The terminal
    uploads the template, which is then sent to every terminal of the employee's areas."""
    emp = db.get(m.Employee, emp_id)
    d = db.get(m.Device, int(data.get("device_id") or 0))
    if not emp or not d:
        raise HTTPException(404, "not found")
    bio_type = int(data.get("bio_type") or 9)
    finger = int(data.get("finger") or 0)
    if bio_type not in ENROLL_TYPES or not 0 <= finger <= 9:
        raise HTTPException(422, "unknown biometric type / finger")
    if not _writing(db):
        raise HTTPException(409, "وضع القراءة فقط: المنفذ 90 مشغول ببرنامج آخر. / Read-only mode.")
    if d.managed_by == "tcp":
        raise HTTPException(409, "هذا الجهاز متصل مباشرة (4370)؛ التسجيل عن بعد يحتاج اتصال الجهاز بالمنفذ 90. / "
                                 "Remote enrollment needs the terminal to push (ADMS).")
    if not is_online(d):
        raise HTTPException(409, "الجهاز غير متصل / The terminal is offline")
    support = sync.device_bio_support(d)
    if support and bio_type not in support and not (bio_type == 2 and 9 in support):
        raise HTTPException(422, "الجهاز لا يدعم هذا النوع / The terminal does not support this type")
    sync.queue(db, d.sn, C.user_update(emp), "User " + emp.emp_code)  # the PIN must exist on the terminal
    cmd = m.DeviceCommand(device_sn=d.sn, title="Remote enroll " + emp.emp_code,
                          content=C.enroll_bio(emp.emp_code, bio_type, finger, not sync._uses_biodata(d)))
    db.add(cmd)
    db.flush()
    audit(db, request, "device.enroll", "employee", f"{emp.emp_code} {ENROLL_TYPES[bio_type]} @ {d.sn}")
    db.commit()
    return {"cmd_id": cmd.id, "device": d.alias or d.sn}


@router.get("/enroll/{cmd_id}")
def enroll_status(cmd_id: int, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """Progress of a remote enrollment: sent -> answered -> template received -> distributed."""
    cmd = db.get(m.DeviceCommand, cmd_id)
    if not cmd or not cmd.content.startswith(("ENROLL_BIO", "ENROLL_FP")):
        raise HTTPException(404, "not found")
    from ..adms.protocol import parse_kv
    kv = parse_kv(cmd.content.split(" ", 1)[1])
    pin = kv.get("pin", "")
    bio_type = int(kv.get("type", 1) or 1)
    emp = db.scalar(select(m.Employee).where(m.Employee.emp_code == pin))
    tpls = []
    if emp:
        types = (2, 9) if bio_type in (2, 9) else (bio_type,)
        tpls = db.scalars(select(m.BioTemplate).where(
            m.BioTemplate.employee_id == emp.id, m.BioTemplate.bio_type.in_(types),
            m.BioTemplate.updated_at >= cmd.created_at)).all()
    sent_to = db.scalars(select(m.DeviceCommand).where(
        m.DeviceCommand.created_at >= cmd.created_at, m.DeviceCommand.device_sn != cmd.device_sn,
        m.DeviceCommand.content.contains(f"={pin}\t"),
        m.DeviceCommand.content.like("DATA UPDATE %"))).all()
    return {"status": cmd.status, "return_code": cmd.return_code, "result": cmd.result,
            "received": len(tpls), "distributed": len({c.device_sn for c in sent_to}),
            "delivered": len({c.device_sn for c in sent_to if c.status == "done"})}


@router.get("/employees/{emp_id}/devices")
def employee_devices(emp_id: int, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """For each terminal of the employee's areas: is everything delivered?"""
    emp = db.get(m.Employee, emp_id)
    if not emp:
        raise HTTPException(404, "not found")
    out = []
    for d in sync.area_devices(db, {a.id for a in emp.areas}):
        cmds = db.scalars(select(m.DeviceCommand).where(
            m.DeviceCommand.device_sn == d.sn,
            m.DeviceCommand.content.contains(f"={emp.emp_code}\t") | m.DeviceCommand.content.endswith(
                f"={emp.emp_code}"))).all()
        pending = sum(c.status in ("pending", "sent") for c in cmds)
        failed = [c.result or c.return_code for c in cmds if c.status == "failed"]
        out.append({"id": d.id, "sn": d.sn, "alias": d.label, "state": dev_dict(db, d)["state"],
                    "link": "4370" if d.managed_by == "tcp" else "ADMS", "pending": pending,
                    "failed": len(failed), "last_error": failed[-1] if failed else "",
                    "support": sorted(sync.device_bio_support(d))})
    return {"rows": out}


@router.get("/devices/{dev_id}/bio-status")
def device_bio_status(dev_id: int, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """What the terminal says it holds vs what has reached the server from it, per type."""
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    got = dict(db.execute(select(m.BioTemplate.bio_type, func.count()).where(
        m.BioTemplate.source_sn == d.sn).group_by(m.BioTemplate.bio_type)).all())
    photos = db.scalar(select(func.count()).select_from(m.BioPhoto).where(m.BioPhoto.source_sn == d.sn)) or 0
    pending = db.scalar(select(func.count()).select_from(m.DeviceCommand).where(
        m.DeviceCommand.device_sn == d.sn, m.DeviceCommand.content.like("DATA QUERY tablename=%"),
        m.DeviceCommand.status.in_(("pending", "sent")))) or 0
    return {"state": dev_dict(db, d)["state"], "link": "4370" if d.managed_by == "tcp" else "ADMS",
            "rows": [
                {"key": "fp", "device": d.fp_count, "server": got.get(1, 0)},
                {"key": "face", "device": d.face_count, "server": got.get(9, 0) + got.get(2, 0)},
                {"key": "palm", "device": d.palm_count, "server": got.get(8, 0)},
                {"key": "photo", "device": None, "server": photos},
            ], "pending": pending}


@router.post("/employees/{emp_id}/pull-bio")
def employee_pull_bio(emp_id: int, request: Request, db: Session = Depends(get_db),
                      _=Depends(require("device.control"))):
    """Fetch one person's fingerprints, face, palm and photo from every terminal of their areas."""
    emp = db.get(m.Employee, emp_id)
    if not emp:
        raise HTTPException(404, "not found")
    if not _writing(db):
        raise HTTPException(409, "وضع القراءة فقط: المنفذ 90 مشغول ببرنامج آخر. / Read-only mode.")
    n, direct = 0, []
    for d in sync.area_devices(db, {a.id for a in emp.areas}):
        if d.managed_by == "tcp":
            direct.append(d.sn)
        elif is_online(d):
            n += sync.pull_bio(db, d, emp.emp_code)
    audit(db, request, "device.pull_bio", "employee", emp.emp_code)
    db.commit()
    from .. import tcp_pull as T
    for sn in direct:
        try:
            T.read_and_store(sn)
        except T.TcpPullError:
            pass
    return {"queued": n, "direct": len(direct)}


@router.get("/employees/{emp_id}/biophoto")
def employee_biophoto(emp_id: int, db: Session = Depends(get_db), _=Depends(require("personnel.view"))):
    """The face enrollment photo the terminal took (visible-light face)."""
    import base64
    from fastapi.responses import Response
    p = db.scalar(select(m.BioPhoto).where(m.BioPhoto.employee_id == emp_id).order_by(m.BioPhoto.bio_type.desc()))
    if not p:
        raise HTTPException(404, "no photo")
    try:
        data = base64.b64decode(p.content)
    except ValueError:
        raise HTTPException(422, "damaged photo")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


# --------------------------------------------------------------------------
# Remote device panel
# --------------------------------------------------------------------------

@router.get("/devices/{dev_id}/panel")
def device_panel(dev_id: int, live: bool = True, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """The terminal's settings. live=false answers at once from what it last reported."""
    from .. import console
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    info = dev_dict(db, d)
    meta = {"sn": d.sn, "alias": d.label, "state": info["state"], "writable": _writing(db), "model": d.model or "",
            "ip": d.ip or "", "users": d.user_count, "faces": d.face_count, "fps": d.fp_count,
            "palms": info.get("palms"), "records": d.att_count, "last_activity": info.get("last_activity")}
    sn = d.sn
    db.close()
    return console.read(sn, live=live) | meta


@router.get("/devices/{dev_id}/panel/option")
def device_panel_option(dev_id: int, key: str, db: Session = Depends(get_db), _=Depends(require("device.view"))):
    """Any setting by its name, read live from the terminal."""
    from .. import console
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    sn = d.sn
    db.close()
    try:
        v = console.read_option(sn, key.strip())
    except console.ConsoleError:
        raise HTTPException(409, "busy")
    return {"key": key.strip(), "value": v, "live": v is not None}


@router.post("/devices/{dev_id}/panel/time")
def device_panel_time(dev_id: int, request: Request, data: dict = Body(default={}), db: Session = Depends(get_db),
                      _=Depends(require("device.control"))):
    """Set the terminal clock: to this PC's time, or to the time given."""
    from .. import console
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    if not _writing(db):
        raise HTTPException(409, "وضع القراءة فقط: المنفذ 90 مشغول ببرنامج آخر. / Read-only mode.")
    when = now()
    if data.get("time"):
        try:
            when = datetime.fromisoformat(str(data["time"]).replace("T", " "))
        except ValueError:
            raise HTTPException(422, "bad time")
    audit(db, request, "device.time", "device", f"{d.sn}: {when:%Y-%m-%d %H:%M:%S}")
    db.commit()
    sn = d.sn
    try:
        if console.set_time(sn, when):
            return {"applied": when.strftime("%Y-%m-%d %H:%M:%S")}
    except console.ConsoleError:
        raise HTTPException(409, "الجهاز مشغول الآن، حاول بعد دقيقة. / The terminal is busy, try again in a minute.")
    sync.queue(db, sn, C.set_time(when), "Set time")   # at the terminal's next push connection
    db.commit()
    return {"queued": 1}


@router.post("/devices/{dev_id}/panel")
def device_panel_write(dev_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
                       _=Depends(require("device.control"))):
    from .. import console
    d = db.get(m.Device, dev_id)
    if not d:
        raise HTTPException(404, "not found")
    if not _writing(db):
        raise HTTPException(409, "وضع القراءة فقط: المنفذ 90 مشغول ببرنامج آخر. / Read-only mode.")
    changes = data.get("options") or {}
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(422, "nothing to change")
    audit(db, request, "device.panel", "device", f"{d.sn}: " + ", ".join(f"{k}={v}" for k, v in changes.items())[:400])
    db.commit()
    sn = d.sn
    db.close()
    try:
        return console.write(sn, changes)
    except console.ConsoleError as exc:
        raise HTTPException(502, str(exc))
