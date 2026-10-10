"""Owned background report exports; administrators can manage all exports."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from ..report_jobs import MAX_ACTIVE, MAX_SAVED, ReportJobError, get_manager, validate_options
from ..security import user_permissions
from .deps import audit, current_user

router = APIRouter(prefix="/api/report-jobs", tags=["report exports"])


def _authorized(user: m.User = Depends(current_user)) -> m.User:
    if not {"reports.view", "system.admin"} & user_permissions(user):
        raise HTTPException(403, "permission required: reports.view")
    return user


def _admin(user: m.User) -> bool:
    return "system.admin" in user_permissions(user)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ReportJobError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("")
def list_exports(user: m.User = Depends(_authorized)):
    return {"jobs": get_manager().list(user.id, _admin(user)), "limits": {"active": MAX_ACTIVE, "saved": MAX_SAVED}}


@router.post("", status_code=202)
def start_export(request: Request, data: dict = Body(...), user: m.User = Depends(_authorized), db: Session = Depends(get_db)):
    try:
        config = validate_options(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    def accepted(job_id):
        audit(db, request, "report.export.start", job_id,
              f"key={config['key']} start={config['start']} end={config['end']} fmt={config['fmt']}")
        db.commit()

    return _call(get_manager().start, config, user.id, on_accept=accepted)


@router.get("/{job_id}")
def get_export(job_id: str, user: m.User = Depends(_authorized)):
    return _call(get_manager().get, job_id, user.id, _admin(user))


@router.post("/{job_id}/cancel")
def cancel_export(job_id: str, request: Request, user: m.User = Depends(_authorized), db: Session = Depends(get_db)):
    job = _call(get_manager().cancel, job_id, user.id, _admin(user))
    audit(db, request, "report.export.cancel", job_id)
    db.commit()
    return job


@router.delete("/{job_id}")
def delete_export(job_id: str, request: Request, user: m.User = Depends(_authorized), db: Session = Depends(get_db)):
    _call(get_manager().delete, job_id, user.id, _admin(user))
    audit(db, request, "report.export.delete", job_id)
    db.commit()
    return {"ok": True}


@router.get("/{job_id}/download")
def download_export(job_id: str, user: m.User = Depends(_authorized)):
    manager = get_manager()
    path = _call(manager.download_path, job_id, user.id, _admin(user))
    job = _call(manager.get, job_id, user.id, _admin(user))
    media_type = "text/csv" if path.suffix == ".csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path, filename=job["result"]["filename"], media_type=media_type)
