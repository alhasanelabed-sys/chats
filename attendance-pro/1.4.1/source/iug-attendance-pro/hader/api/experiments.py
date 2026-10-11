"""Administrator-only controls for synthetic workload experiments."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import store
from ..db import get_db
from ..experiment_config import LIMITS, validate_options
from ..experiments import ExperimentError, get_manager
from .deps import audit, require

router = APIRouter(prefix="/api/experiments", tags=["experiments"], dependencies=[Depends(require("system.admin"))])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ExperimentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("")
def list_experiments(db: Session = Depends(get_db)):
    return {"jobs": get_manager().list(), "limits": LIMITS, "write_back": bool(store.get(db, "tcp.write_back"))}


@router.post("", status_code=202)
def start_experiment(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    try:
        config = validate_options(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    def accepted():
        if config["keep_write_off"]:
            store.set_(db, "tcp.write_back", False)
        audit(db, request, "experiment.start", "experiment",
              f"employees={config['employees']}, devices={config['devices']}, days={config['days']}, "
              f"keep_write_off={config['keep_write_off']}")
        db.commit()

    return _call(get_manager().start, config, on_accept=accepted)


@router.get("/{job_id}")
def get_experiment(job_id: str):
    return _call(get_manager().get, job_id)


@router.post("/{job_id}/cancel")
def cancel_experiment(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = _call(get_manager().cancel, job_id)
    audit(db, request, "experiment.cancel", "experiment", job_id)
    db.commit()
    return job


@router.delete("/{job_id}")
def delete_experiment(job_id: str, request: Request, db: Session = Depends(get_db)):
    _call(get_manager().delete, job_id)
    audit(db, request, "experiment.delete", "experiment", job_id)
    db.commit()
    return {"ok": True}


@router.get("/{job_id}/download")
def download_experiment(job_id: str, kind: str = "json"):
    path = _call(get_manager().download_path, job_id, kind)
    return FileResponse(path, filename=f"experiment_{job_id[:8]}.{kind}",
                        media_type="text/csv" if kind == "csv" else "application/json")
