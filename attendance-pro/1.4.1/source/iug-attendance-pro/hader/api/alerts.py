"""Alerts settings, test messages, the alerts log, and the bell (in-app notifications)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import alerts as A
from .. import models as m
from .. import store
from ..db import get_db, now
from .deps import audit, current_user, require, ser

router = APIRouter(prefix="/api")

CHANNEL_KEYS = ("alerts.enabled", "alerts.language", "alerts.quiet_from", "alerts.quiet_to", "alerts.smtp_host",
                "alerts.smtp_port", "alerts.smtp_security", "alerts.smtp_user", "alerts.smtp_password",
                "alerts.smtp_from", "alerts.email_employees", "alerts.telegram_token", "alerts.webhook_url")
SECRET = ("alerts.smtp_password", "alerts.telegram_token")
MASK = "********"


@router.get("/alerts/config")
def get_config(db: Session = Depends(get_db), _=Depends(require("system.admin"))):
    cfg = {k: store.get(db, k) for k in CHANNEL_KEYS}
    for k in SECRET:
        if cfg.get(k):
            cfg[k] = MASK
    return {"rules": A.rules(db), "settings": cfg}


@router.put("/alerts/config")
def put_config(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
               _=Depends(require("system.admin"))):
    rules = data.get("rules")
    if isinstance(rules, dict):
        clean = {}
        for k, v in rules.items():
            if k in A.DEFAULT_RULES and isinstance(v, dict):
                clean[k] = {f: v[f] for f in A.DEFAULT_RULES[k] if f in v}
        store.set_(db, "alerts.rules", clean)
    for k, v in (data.get("settings") or {}).items():
        if k in CHANNEL_KEYS and not (k in SECRET and v == MASK):
            store.set_(db, k, v)
    audit(db, request, "update", "alerts", ", ".join(list(rules or {}) + list(data.get("settings") or {}))[:400])
    db.commit()
    return get_config(db)


@router.post("/alerts/test")
def test_channel(data: dict = Body(...), db: Session = Depends(get_db), _=Depends(require("system.admin"))):
    try:
        A.send_test(db, str(data.get("channel")), str(data.get("target") or ""))
    except Exception as exc:  # noqa: BLE001 - shown to the admin as the reason
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True}


@router.get("/alerts/log")
def alert_log(rule: str = "", status: str = "", limit: int = 200, db: Session = Depends(get_db),
              _=Depends(require("system.admin"))):
    q = select(m.AlertLog)
    if rule:
        q = q.where(m.AlertLog.rule == rule)
    if status:
        q = q.where(m.AlertLog.status == status)
    rows = db.scalars(q.order_by(m.AlertLog.id.desc()).limit(max(1, min(limit, 1000)))).all()
    return {"rows": [ser(r) for r in rows]}


# ---------------------------------------------------------------- the bell

@router.get("/notifications")
def my_notifications(limit: int = 30, db: Session = Depends(get_db), user: m.User = Depends(current_user)):
    rows = db.scalars(select(m.Notification).where(m.Notification.to_kind == "user", m.Notification.to_id == user.id)
                      .order_by(m.Notification.id.desc()).limit(max(1, min(limit, 200)))).all()
    unread = db.scalar(select(func.count()).select_from(m.Notification).where(
        m.Notification.to_kind == "user", m.Notification.to_id == user.id, m.Notification.read_at.is_(None))) or 0
    return {"unread": unread, "rows": [ser(r) for r in rows]}


@router.post("/notifications/read")
def mark_read(data: dict = Body(default={}), db: Session = Depends(get_db), user: m.User = Depends(current_user)):
    q = update(m.Notification).where(m.Notification.to_kind == "user", m.Notification.to_id == user.id,
                                     m.Notification.read_at.is_(None))
    if data.get("ids"):
        q = q.where(m.Notification.id.in_([int(x) for x in data["ids"]]))
    db.execute(q.values(read_at=now()))
    db.commit()
    return {"ok": True}


@router.put("/me-user/telegram")
def set_telegram(data: dict = Body(...), db: Session = Depends(get_db), user: m.User = Depends(current_user)):
    user.telegram_chat_id = str(data.get("chat_id") or "").strip()[:40]
    db.commit()
    return {"ok": True}


