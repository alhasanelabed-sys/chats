"""Alerts: the right people hear about what matters, once, through the channels they use.

Rules (each can be switched off and tuned in System > Alerts):

* late            an employee's first punch is late           -> the employee (+ managers)
* absent          scheduled, no punch by a set time            -> the employee + managers
* missing_out     punched in, no punch out by a set time       -> the employee
* device_offline  a terminal silent for N minutes              -> managers
* device_online   it is back                                   -> managers
* request_new     a portal request waits for approval          -> approvers
* request_decided a request was approved / rejected            -> the employee
* daily_summary   the day so far, at a set time                -> managers (+ e-mail list)
* weekly_report   last 7 days as an Excel file                 -> e-mail list

Channels: in-app (bell for users, inbox in the employee portal) always; e-mail (SMTP),
Telegram (bot) and a webhook (for WhatsApp/SMS gateways) when configured. Every alert
has a key: the same event is never sent twice. During quiet hours only in-app is used.
"""
from __future__ import annotations

import json
import logging
import queue
import smtplib
import ssl
import threading
import urllib.request
from datetime import datetime, time, timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from . import store
from .db import now, session_scope

log = logging.getLogger("hader.alerts")

DEFAULT_RULES: dict[str, dict] = {
    "late": {"on": True, "min_minutes": 1, "to_employee": True, "to_managers": False},
    "absent": {"on": True, "at": "10:00", "to_employee": True, "to_managers": True},
    "missing_out": {"on": True, "at": "20:00", "to_employee": True, "to_managers": False},
    "device_offline": {"on": True, "after_minutes": 10, "to_managers": True},
    "device_online": {"on": True, "to_managers": True},
    "request_new": {"on": True, "to_managers": True},
    "request_decided": {"on": True, "to_employee": True},
    "daily_summary": {"on": False, "at": "09:30", "to_managers": True, "emails": ""},
    "weekly_report": {"on": False, "weekday": 6, "at": "08:00", "emails": ""},  # 6 = Sunday
}

TEXT = {  # (Arabic, English); {placeholders} filled per alert
    "late": ("تأخير اليوم", "Late today",
             "{name}: أول بصمة {time} — متأخر {minutes} دقيقة.", "{name}: first punch {time} — {minutes} min late."),
    "absent": ("غياب", "Absent", "{name}: لا توجد بصمة حتى {time}.", "{name}: no punch by {time}."),
    "missing_out": ("بصمة خروج ناقصة", "Missing check-out",
                    "{name}: بصمة دخول {time} بدون خروج. قدّم طلب تصحيح إن لزم.",
                    "{name}: checked in at {time} with no check-out. Send a correction request if needed."),
    "device_offline": ("جهاز غير متصل", "Terminal offline",
                       "{device} لم يتصل منذ {minutes} دقيقة (آخر اتصال {time}).",
                       "{device} has been silent for {minutes} min (last contact {time})."),
    "device_online": ("عاد الجهاز للاتصال", "Terminal back online", "{device} متصل الآن.", "{device} is online again."),
    "request_new": ("طلب جديد بانتظار الموافقة", "New request to approve",
                    "{name}: {what} — {when}.", "{name}: {what} — {when}."),
    "request_decided": ("تم الرد على طلبك", "Your request was answered",
                        "{what} ({when}): {decision}.", "{what} ({when}): {decision}."),
    "daily_summary": ("ملخص اليوم", "Today so far",
                      "حضر {present} من {expected} ({rate}) · متأخر {late} · غائب {absent} · أجهزة غير متصلة {offline}.",
                      "{present} of {expected} in ({rate}) · late {late} · absent {absent} · terminals offline {offline}."),
    "weekly_report": ("التقرير الأسبوعي", "Weekly report",
                      "ملخص الحضور من {start} إلى {end} مرفق.", "Attendance summary {start} to {end} attached."),
}
LEVEL = {"late": "warn", "absent": "bad", "missing_out": "warn", "device_offline": "bad", "device_online": "good",
         "request_new": "info", "request_decided": "info", "daily_summary": "info", "weekly_report": "info"}
LINK_USER = {"late": "#/att/calc", "absent": "#/att/calc", "missing_out": "#/att/calc",
             "device_offline": "#/device/terminals", "device_online": "#/device/terminals",
             "request_new": "#/att/leaves", "daily_summary": "#/dashboard", "weekly_report": "#/reports"}


def rules(db: Session) -> dict[str, dict]:
    saved = store.get(db, "alerts.rules") or {}
    return {k: {**v, **(saved.get(k) or {})} for k, v in DEFAULT_RULES.items()}


def _fmt(rule: str, lang: str, **kw) -> tuple[str, str]:
    t = TEXT[rule]
    title, body = (t[0], t[2]) if lang != "en" else (t[1], t[3])
    try:
        return title, body.format(**kw)
    except (KeyError, IndexError):
        return title, body


def managers(db: Session) -> list[m.User]:
    from .security import user_permissions
    return [u for u in db.scalars(select(m.User).where(m.User.active.is_(True))).all()
            if {"system.admin", "attendance.approve"} & user_permissions(u)]


def _quiet(db: Session) -> bool:
    a, b = store.get(db, "alerts.quiet_from") or "", store.get(db, "alerts.quiet_to") or ""
    if not a or not b:
        return False
    try:
        t, qa, qb = now().time(), time.fromisoformat(a), time.fromisoformat(b)
    except ValueError:
        return False
    return qa <= t < qb if qa < qb else (t >= qa or t < qb)


def already(db: Session, key: str) -> bool:
    return db.scalar(select(m.AlertLog.id).where(m.AlertLog.key == key).limit(1)) is not None


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

_outbox: "queue.Queue[tuple]" = queue.Queue()
_sender_started = threading.Event()


def _start_sender() -> None:
    if _sender_started.is_set():
        return
    _sender_started.set()
    threading.Thread(target=_sender_loop, daemon=True, name="alerts-sender").start()


def _sender_loop() -> None:
    while True:
        job = _outbox.get()
        try:
            _deliver(*job)
        except Exception:  # noqa: BLE001
            log.exception("alert delivery failed")


def _log(key: str, rule: str, channel: str, target: str, title: str, status: str, error: str = "") -> None:
    with session_scope() as db:
        db.add(m.AlertLog(key=key[:160], rule=rule, channel=channel, target=target[:160], title=title[:200],
                          status=status, error=error[:400]))


def _deliver(channel: str, key: str, rule: str, target: str, title: str, body: str, attachment=None) -> None:
    try:
        with session_scope() as db:
            cfg = {k: store.get(db, k) for k in ("alerts.smtp_host", "alerts.smtp_port", "alerts.smtp_user",
                                                 "alerts.smtp_password", "alerts.smtp_from", "alerts.smtp_security",
                                                 "alerts.telegram_token", "alerts.webhook_url", "company.name")}
        if channel == "email":
            send_email(cfg, target, title, body, attachment)
        elif channel == "telegram":
            send_telegram(cfg, target, f"{title}\n{body}")
        elif channel == "webhook":
            send_webhook(cfg, {"rule": rule, "title": title, "body": body, "to": target, "key": key})
        _log(key, rule, channel, target, title, "sent")
    except Exception as exc:  # noqa: BLE001 - recorded for the alerts log
        _log(key, rule, channel, target, title, "failed", f"{type(exc).__name__}: {exc}")


def send_email(cfg: dict, to: str, subject: str, body: str, attachment=None) -> None:
    host = cfg.get("alerts.smtp_host") or ""
    if not host:
        raise RuntimeError("SMTP server is not set")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("alerts.smtp_from") or cfg.get("alerts.smtp_user") or "hader@localhost"
    msg["To"] = to
    msg.set_content(body)
    msg.add_alternative(f'<div dir="auto" style="font-family:Segoe UI,Tahoma,sans-serif;font-size:15px">'
                        f'<h3 style="margin:0 0 8px">{_html(subject)}</h3><p>{_html(body)}</p>'
                        f'<p style="color:#888;font-size:12px">{_html(cfg.get("company.name") or "Hader")}</p></div>',
                        subtype="html")
    if attachment:
        name, data = attachment
        msg.add_attachment(data, maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=name)
    port = int(cfg.get("alerts.smtp_port") or 587)
    sec = cfg.get("alerts.smtp_security") or ("ssl" if port == 465 else "starttls")
    ctx = ssl.create_default_context()
    if sec == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        if sec == "starttls":
            server.starttls(context=ctx)
    with server:
        if cfg.get("alerts.smtp_user"):
            server.login(cfg["alerts.smtp_user"], cfg.get("alerts.smtp_password") or "")
        server.send_message(msg)


def send_telegram(cfg: dict, chat_id: str, text: str) -> None:
    token = cfg.get("alerts.telegram_token") or ""
    if not token:
        raise RuntimeError("Telegram bot token is not set")
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")


def send_webhook(cfg: dict, payload: dict) -> None:
    url = cfg.get("alerts.webhook_url") or ""
    if not url:
        raise RuntimeError("webhook address is not set")
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status >= 300:
            raise RuntimeError(f"HTTP {r.status}")


def _html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


# --------------------------------------------------------------------------
# Notify
# --------------------------------------------------------------------------

def notify(db: Session, rule: str, key: str, *, users: list[m.User] = (), employees: list[m.Employee] = (),
           emails: list[str] = (), attachment=None, **kw) -> int:
    """Send one alert (identified by ``key``) to users / employees / extra e-mail addresses.
    Returns how many recipients got it; 0 when this event was already sent."""
    if already(db, key):
        return 0
    quiet = _quiet(db)
    ext = {"email": bool(store.get(db, "alerts.smtp_host")), "telegram": bool(store.get(db, "alerts.telegram_token")),
           "webhook": bool(store.get(db, "alerts.webhook_url"))}
    jobs, n = [], 0
    for u in users:
        title, body = _fmt(rule, u.language or "ar", **kw)
        db.add(m.Notification(to_kind="user", to_id=u.id, kind=rule, level=LEVEL.get(rule, "info"),
                              title=title, body=body, link=LINK_USER.get(rule, "")))
        n += 1
        if not quiet and ext["email"] and u.email:
            jobs.append(("email", key, rule, u.email, title, body, attachment))
        if not quiet and ext["telegram"] and u.telegram_chat_id:
            jobs.append(("telegram", key, rule, u.telegram_chat_id, title, body, None))
    for e in employees:
        title, body = _fmt(rule, e.portal_lang or "ar", **kw)
        db.add(m.Notification(to_kind="employee", to_id=e.id, kind=rule, level=LEVEL.get(rule, "info"),
                              title=title, body=body, link=""))
        n += 1
        if not quiet and ext["email"] and e.email and store.get(db, "alerts.email_employees"):
            jobs.append(("email", key, rule, e.email, title, body, None))
    lang = store.get(db, "alerts.language") or "ar"
    for addr in emails:
        if addr and ext["email"] and not quiet:
            title, body = _fmt(rule, lang, **kw)
            jobs.append(("email", key, rule, addr, title, body, attachment))
            n += 1
    if ext["webhook"] and not quiet:
        title, body = _fmt(rule, lang, **kw)
        jobs.append(("webhook", key, rule, "webhook", title, body, None))
    db.add(m.AlertLog(key=key[:160], rule=rule, channel="inapp", target=f"{len(users)} users, {len(employees)} employees",
                      title=_fmt(rule, "ar", **kw)[0], status="sent"))
    db.flush()
    if jobs:
        _start_sender()
        for j in jobs:
            _outbox.put(j)
    from .events import publish
    publish("notify", rule=rule)
    return n


def _emails(text: str) -> list[str]:
    return [x.strip() for x in (text or "").replace(";", ",").split(",") if "@" in x]


def _hm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else ""


# --------------------------------------------------------------------------
# Events coming from the rest of the program
# --------------------------------------------------------------------------

REQUEST_WHAT = {"leave": ("إجازة", "Leave"), "manual": ("تصحيح بصمة", "Punch correction"),
                "overtime": ("عمل إضافي", "Overtime")}


def _when(obj) -> str:
    if hasattr(obj, "punch_time"):
        return obj.punch_time.strftime("%Y-%m-%d %H:%M")
    a, b = obj.start_time, obj.end_time
    return f"{a:%Y-%m-%d %H:%M} → {b:%Y-%m-%d %H:%M}" if a.date() == b.date() else f"{a:%Y-%m-%d} → {b:%Y-%m-%d}"


def request_submitted(db: Session, kind: str, obj, emp: m.Employee) -> None:
    r = rules(db)["request_new"]
    if not r["on"]:
        return
    what = REQUEST_WHAT[kind][0]
    notify(db, "request_new", f"request_new:{kind}:{obj.id}", users=managers(db) if r.get("to_managers") else [],
           name=emp.full_name, what=what, when=_when(obj))


def request_decided(db: Session, kind: str, obj) -> None:
    r = rules(db)["request_decided"]
    if not r["on"] or obj.status not in ("approved", "rejected"):
        return
    emp = db.get(m.Employee, obj.employee_id)
    if emp is None:
        return
    ar = emp.portal_lang != "en"
    what = REQUEST_WHAT[kind][0 if ar else 1]
    decision = ("تمت الموافقة ✓" if obj.status == "approved" else "مرفوض ✗") if ar else \
        ("approved ✓" if obj.status == "approved" else "rejected ✗")
    notify(db, "request_decided", f"request_decided:{kind}:{obj.id}:{obj.status}",
           employees=[emp] if r.get("to_employee") else [], what=what, when=_when(obj), decision=decision)


# --------------------------------------------------------------------------
# The minute tick
# --------------------------------------------------------------------------

def _at_or_after(hhmm: str) -> bool:
    try:
        return now().time() >= time.fromisoformat(hhmm)
    except ValueError:
        return False


def tick(state: dict) -> None:
    """Called every minute by the maintenance loop."""
    with session_scope() as db:
        if not store.get(db, "alerts.enabled"):
            return
        r = rules(db)
        _devices(db, r)
        if any(r[k]["on"] for k in ("late", "absent", "missing_out", "daily_summary")):
            _attendance(db, r)
        _weekly(db, r)


def _devices(db: Session, r: dict) -> None:
    from .api.devices import is_online
    t = now()
    mgr = None
    for d in db.scalars(select(m.Device).where(m.Device.enabled.is_(True))).all():
        if not d.last_activity:
            continue
        silent = (t - d.last_activity).total_seconds() / 60
        outage = f"{d.sn}:{d.last_activity:%Y%m%d%H%M%S}"
        if r["device_offline"]["on"] and not is_online(d) and silent >= float(r["device_offline"].get("after_minutes") or 10):
            mgr = mgr if mgr is not None else managers(db)
            notify(db, "device_offline", f"device_offline:{outage}", users=mgr if r["device_offline"].get("to_managers") else [],
                   device=d.label, minutes=int(silent), time=d.last_activity.strftime("%H:%M"))
        elif is_online(d) and r["device_online"]["on"]:
            # back online after an outage that was reported: say so once
            last = db.scalar(select(m.AlertLog.key).where(m.AlertLog.rule == "device_offline",
                                                         m.AlertLog.key.like(f"device_offline:{d.sn}:%"))
                             .order_by(m.AlertLog.id.desc()).limit(1))
            if last and not already(db, "device_online:" + last.split(":", 1)[1]):
                mgr = mgr if mgr is not None else managers(db)
                notify(db, "device_online", "device_online:" + last.split(":", 1)[1],
                       users=mgr if r["device_online"].get("to_managers") else [], device=d.label)


def _attendance(db: Session, r: dict) -> None:
    from .engine import Engine
    today = now().date()
    rows = Engine(db, today, today, include_resigned=False).run()
    emps = {e.id: e for e in db.scalars(select(m.Employee).where(m.Employee.status == "active")).all()}
    mgr = managers(db)
    day = today.isoformat()
    if r["late"]["on"]:
        for x in rows:
            if x.late and x.late >= int(r["late"].get("min_minutes") or 1) and x.clock_in:
                e = emps.get(x.employee_id)
                if e:
                    notify(db, "late", f"late:{e.id}:{day}", employees=[e] if r["late"].get("to_employee") else [],
                           users=mgr if r["late"].get("to_managers") else [],
                           name=e.full_name, time=_hm(x.clock_in), minutes=x.late)
    if r["absent"]["on"] and _at_or_after(r["absent"]["at"]):
        for x in rows:
            if x.status in ("absent", "pending") and not x.punches and x.sched_in:
                e = emps.get(x.employee_id)
                if e:
                    notify(db, "absent", f"absent:{e.id}:{day}", employees=[e] if r["absent"].get("to_employee") else [],
                           users=mgr if r["absent"].get("to_managers") else [], name=e.full_name, time=r["absent"]["at"])
    if r["missing_out"]["on"] and _at_or_after(r["missing_out"]["at"]):
        for x in rows:
            if x.clock_in and not x.clock_out:
                e = emps.get(x.employee_id)
                if e:
                    notify(db, "missing_out", f"missing_out:{e.id}:{day}",
                           employees=[e] if r["missing_out"].get("to_employee") else [],
                           users=mgr if r["missing_out"].get("to_managers") else [], name=e.full_name,
                           time=_hm(x.clock_in))
    ds = r["daily_summary"]
    if ds["on"] and _at_or_after(ds["at"]):
        expected = sum(1 for x in rows if x.status not in ("off", "holiday", "leave"))
        present = sum(1 for x in rows if x.clock_in or x.clock_out)
        from .api.devices import is_online
        offline = sum(1 for d in db.scalars(select(m.Device).where(m.Device.enabled.is_(True))).all() if not is_online(d))
        notify(db, "daily_summary", f"daily_summary:{day}", users=mgr if ds.get("to_managers") else [],
               emails=_emails(ds.get("emails")), present=present, expected=expected,
               rate=f"{round(100 * present / expected)}%" if expected else "—",
               late=sum(1 for x in rows if x.late), absent=sum(1 for x in rows if x.status == "absent"),
               offline=offline)


def _weekly(db: Session, r: dict) -> None:
    w = r["weekly_report"]
    t = now()
    if not w["on"] or t.weekday() != int(w.get("weekday", 6)) or not _at_or_after(w.get("at") or "08:00"):
        return
    key = f"weekly_report:{t.date().isoformat()}"
    if already(db, key) or not _emails(w.get("emails")):
        return
    from . import reports as R
    end = t.date() - timedelta(days=1)
    start = end - timedelta(days=6)
    lang = store.get(db, "alerts.language") or "ar"
    rep = R.build(db, "summary", start, end, lang=lang)
    data = R.to_xlsx(rep, store.get(db, "company.name") or "", rtl=lang != "en")
    notify(db, "weekly_report", key, emails=_emails(w.get("emails")),
           attachment=(f"attendance_{start}_{end}.xlsx", data), start=start.isoformat(), end=end.isoformat())


def send_test(db: Session, channel: str, target: str) -> None:
    """Send a test message now (raises with the reason when it fails)."""
    cfg = {k: store.get(db, k) for k in ("alerts.smtp_host", "alerts.smtp_port", "alerts.smtp_user",
                                         "alerts.smtp_password", "alerts.smtp_from", "alerts.smtp_security",
                                         "alerts.telegram_token", "alerts.webhook_url", "company.name")}
    text = "رسالة تجربة من نظام الحضور — Test message from the attendance system"
    if channel == "email":
        send_email(cfg, target, "Hader — test", text)
    elif channel == "telegram":
        send_telegram(cfg, target, text)
    elif channel == "webhook":
        send_webhook(cfg, {"rule": "test", "title": "test", "body": text})
    else:
        raise ValueError("unknown channel")

