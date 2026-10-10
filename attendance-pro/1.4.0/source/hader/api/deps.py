"""Shared API helpers: authentication, permissions, paging, audit."""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from .. import models as m
from ..security import read_token, token_password_matches, user_permissions

COOKIE = os.environ.get("HADER_SESSION_COOKIE", "app_session")
_PASSWORD_CHANGE_PATHS = {("GET", "/api/auth/me"), ("POST", "/api/auth/password"),
                          ("POST", "/api/auth/logout")}


def current_user(request: Request, db: Session = Depends(get_db)) -> m.User:
    token = request.cookies.get(COOKIE, "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    data = read_token(token) if token else None
    if not data or data.get("k"):  # an employee-portal token never opens the admin side
        raise HTTPException(401, "not authenticated")
    user = db.get(m.User, data["u"])
    if not user or not user.active or not token_password_matches(data, user.password_hash):
        raise HTTPException(401, "not authenticated")
    request.state.user = user
    if user.must_change_password and (request.method, request.url.path) not in _PASSWORD_CHANGE_PATHS:
        raise HTTPException(403, {"code": "password_change_required",
                                  "message": "غيّر كلمة المرور أولاً / Change your password first"})
    return user


def require(perm: str):
    def dep(user: m.User = Depends(current_user)) -> m.User:
        if perm not in user_permissions(user):
            raise HTTPException(403, f"permission required: {perm}")
        return user
    return dep


def audit(db: Session, request: Request | None, action: str, target: str = "", detail: str = "") -> None:
    user = getattr(request.state, "user", None) if request is not None else None
    ip = request.client.host if request is not None and request.client else ""
    db.add(m.AuditLog(username=user.username if user else "system", action=action, target=target[:60],
                      detail=detail[:20000], ip=ip))


def page(db: Session, q, offset: int, limit: int):
    total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    rows = db.execute(q.offset(max(0, offset)).limit(max(1, min(limit, 5000)))).all()
    return total or 0, rows


def ser(obj, fields: list[str] | None = None) -> dict[str, Any]:
    """Column values of an ORM object as JSON-friendly dict."""
    out = {}
    for col in obj.__table__.columns:
        if fields and col.key not in fields:
            continue
        v = getattr(obj, col.key)
        if isinstance(v, datetime):
            v = v.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(v, date):
            v = v.isoformat()
        elif hasattr(v, "strftime"):
            v = v.strftime("%H:%M")
        out[col.key] = v
    return out


def parse_date(v: str | None, default: date | None = None) -> date | None:
    if not v:
        return default
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(422, f"invalid date: {v}")


def parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    v = str(v).replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    raise HTTPException(422, f"invalid datetime: {v}")


def parse_time_str(v: str | None):
    from datetime import time
    if v in (None, ""):
        return None
    try:
        parts = [int(x) for x in str(v).split(":")[:2]]
        return time(parts[0], parts[1] if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        raise HTTPException(422, f"invalid time: {v}")


def ids_param(v: str | None) -> list[int] | None:
    if not v:
        return None
    try:
        return [int(x) for x in str(v).split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "invalid id list")
