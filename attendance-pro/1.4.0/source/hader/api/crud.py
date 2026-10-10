"""Generic list/create/update/delete endpoints for the simple tables."""
from __future__ import annotations

from typing import Callable
import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, Time, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from .deps import audit, page, parse_date, parse_dt, parse_time_str, require, ser


def _audit_values(obj) -> dict:
    protected = {"photo", "password_hash", "portal_hash", "dev_password", "template", "content", "token"}
    values = {}
    for col in obj.__table__.columns:
        if col.key in protected or any(part in col.key for part in ("password", "secret", "token")):
            continue
        value = getattr(obj, col.key)
        values[col.key] = value if value is None or type(value) in {bool, int, float} else str(value)[:300]
    return values


def coerce(model, data: dict, partial: bool = False) -> dict:
    out = {}
    cols = {c.key: c for c in model.__table__.columns}
    for key, value in data.items():
        col = cols.get(key)
        if col is None or key in ("id", "created_at", "updated_at", "password_hash"):
            continue
        t = col.type
        if value == "" and col.nullable and not isinstance(t, (String, Text)):
            value = None
        try:
            if value is None:
                pass
            elif isinstance(t, Boolean):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif isinstance(t, Integer):
                value = int(value)
            elif isinstance(t, Float):
                value = float(value)
            elif isinstance(t, DateTime):
                value = parse_dt(value)
            elif isinstance(t, Date):
                value = parse_date(value)
            elif isinstance(t, Time):
                value = parse_time_str(value)
            else:
                value = str(value).strip()
        except (TypeError, ValueError):
            raise HTTPException(422, f"invalid value for {key}")
        out[key] = value
    return out


def crud_router(model, path: str, view_perm: str, edit_perm: str, *,
                search: tuple[str, ...] = (), order=None,
                to_dict: Callable | None = None,
                before_save: Callable | None = None,
                after_save: Callable | None = None,
                mutation_guard: Callable | None = None,
                filters: tuple[str, ...] = ()) -> APIRouter:
    """Build CRUD routes.

    ``mutation_guard(db, request, user, obj, changes, action)`` runs before
    supplied values are applied, for create/update/delete. ``changes`` contains
    coerced column values and may be filtered or amended by the guard. On update
    and delete, ``obj`` still contains the persisted values, so a guard can check
    protected state transitions without trusting the client's previous state.
    """
    r = APIRouter(prefix=path)
    dump = to_dict or (lambda db, o: ser(o))

    @r.get("")
    def list_(request: Request, q: str = "", offset: int = 0, limit: int = 200,
              db: Session = Depends(get_db), _=Depends(require(view_perm))):
        stmt = select(model)
        if q and search:
            stmt = stmt.where(or_(*[getattr(model, f).ilike(f"%{q}%") for f in search]))
        for f in filters:
            v = request.query_params.get(f)
            if v not in (None, ""):
                col = getattr(model, f)
                stmt = stmt.where(col.in_([int(x) for x in v.split(",")])) if f.endswith("_id") else stmt.where(col == v)
        stmt = stmt.order_by(order if order is not None else model.id)
        total, rows = page(db, stmt, offset, limit)
        return {"total": total, "rows": [dump(db, row[0]) for row in rows]}

    @r.get("/{obj_id}")
    def get_(obj_id: int, db: Session = Depends(get_db), _=Depends(require(view_perm))):
        obj = db.get(model, obj_id)
        if not obj:
            raise HTTPException(404, "not found")
        return dump(db, obj)

    @r.post("")
    def create(request: Request, data: dict = Body(...), db: Session = Depends(get_db),
               user=Depends(require(edit_perm))):
        changes = coerce(model, data)
        obj = model()
        for col in model.__table__.columns:  # make column defaults visible to validators
            if getattr(obj, col.key) is None and col.default is not None and col.default.is_scalar:
                setattr(obj, col.key, col.default.arg)
        if mutation_guard:
            mutation_guard(db, request, user, obj, changes, "create")
        for key, value in changes.items():
            setattr(obj, key, value)
        if before_save:
            before_save(db, obj, data, True)
        db.add(obj)
        try:
            db.flush()
            if after_save:
                after_save(db, obj, data, True)
            audit(db, request, "create", model.__tablename__, json.dumps({"id": obj.id, "after": _audit_values(obj)}, ensure_ascii=False))
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "duplicate or invalid reference")
        return dump(db, obj)

    @r.put("/{obj_id}")
    def update(obj_id: int, request: Request, data: dict = Body(...), db: Session = Depends(get_db),
               user=Depends(require(edit_perm))):
        obj = db.get(model, obj_id)
        if not obj:
            raise HTTPException(404, "not found")
        changes = coerce(model, data, partial=True)
        before = _audit_values(obj)
        db.info["policy_actor"] = user.username
        if mutation_guard:
            mutation_guard(db, request, user, obj, changes, "update")
        for k, v in changes.items():
            setattr(obj, k, v)
        if before_save:
            before_save(db, obj, data, False)
        try:
            db.flush()
            if after_save:
                after_save(db, obj, data, False)
            after = _audit_values(obj)
            changed = [key for key in after if before.get(key) != after[key]]
            audit(db, request, "update", model.__tablename__, json.dumps({"id": obj.id,
                  "before": {key: before.get(key) for key in changed}, "after": {key: after[key] for key in changed}}, ensure_ascii=False))
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "duplicate or invalid reference")
        return dump(db, obj)

    @r.delete("/{obj_id}")
    def delete(obj_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require(edit_perm))):
        obj = db.get(model, obj_id)
        if not obj:
            raise HTTPException(404, "not found")
        if mutation_guard:
            mutation_guard(db, request, user, obj, {}, "delete")
        before = _audit_values(obj)
        db.delete(obj)
        try:
            audit(db, request, "delete", model.__tablename__, json.dumps({"id": obj_id, "before": before}, ensure_ascii=False))
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "in use — remove the records that reference it first")
        return {"ok": True}

    return r
