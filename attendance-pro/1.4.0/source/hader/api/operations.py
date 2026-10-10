"""Operations visibility; clock measurements read without setting a terminal."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models as m, operability as O, store
from ..config import settings
from ..db import get_db
from ..security import user_permissions
from .deps import audit, current_user, require

router = APIRouter(prefix="/api/operations")


def _can_view(user=Depends(current_user)):
    permissions = user_permissions(user)
    if not {"device.view", "system.admin"}.intersection(permissions):
        raise HTTPException(403, "permission required: device.view or system.admin")
    return user


@router.get("/health")
def get_health(db: Session = Depends(get_db), _=Depends(_can_view)):
    return O.health(db)


@router.post("/devices/{device_id}/clock-check")
def clock_check(device_id: int, request: Request, db: Session = Depends(get_db),
                _=Depends(require("device.control"))):
    device = db.get(m.Device, device_id)
    if not device:
        raise HTTPException(404, "not found")
    if not device.enabled:
        raise HTTPException(409, "الجهاز معطل / Device is disabled")
    try:
        state = O.check_clock(device)
    except O.OperationError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit(db, request, "device.clock_check", "device", device.sn + ": " + state["status"])
    db.commit()
    return state


@router.post("/backup-check", status_code=202)
def backup_check(request: Request, data: dict = Body(default={}), db: Session = Depends(get_db),
                 _=Depends(require("system.admin"))):
    if set(data) - {"name"}:
        raise HTTPException(422, "unknown backup option")
    name = data.get("name")
    if name is None:
        files = sorted([*settings.backups_dir.glob("hader_*.zip"), *settings.backups_dir.glob("hader_*.db")])
        if not files:
            raise HTTPException(404, "لا توجد نسخة محفوظة / No saved backup")
        path = files[-1]
    else:
        if not isinstance(name, str) or Path(name).name != name or "\\" in name or name in {"", ".", ".."}:
            raise HTTPException(422, "invalid backup filename")
        path = settings.backups_dir / name
    try:
        state = O.backup_manager().start(path, store.get(db, "backup.offsite_path", "") or "")
    except O.OperationError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "تعذر بدء التحقق / Could not start verification") from exc
    audit(db, request, "backup.check", "system", path.name)
    db.commit()
    return state


@router.post("/backup-check/cancel")
def cancel_backup_check(request: Request, db: Session = Depends(get_db), _=Depends(require("system.admin"))):
    state = O.backup_manager().cancel()
    audit(db, request, "backup.check_cancel", "system")
    db.commit()
    return state
