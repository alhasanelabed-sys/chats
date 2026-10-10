"""Authorized diagnostics and audited replay of attendance-only uploads."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import intake
from ..db import get_db
from ..security import user_permissions
from .deps import audit, current_user, require

router = APIRouter(prefix="/api/intake", tags=["intake"])


def _read_permission(user=Depends(current_user)):
    if not ({"system.admin", "device.view"} & set(user_permissions(user))):
        raise HTTPException(403, "permission required: device.view")
    return user


def _batch(db, batch_id):
    item = db.get(intake.IntakeBatch, batch_id)
    if item is None:
        raise HTTPException(404, "attendance upload not found")
    return item


@router.get("")
def list_batches(sn: str = "", rejected_only: bool = False, offset: int = 0, limit: int = 200,
                 db: Session = Depends(get_db), _=Depends(_read_permission)):
    statement = select(intake.IntakeBatch)
    if sn:
        statement = statement.where(intake.IntakeBatch.source_sn == sn[:50])
    if rejected_only:
        statement = statement.where(intake.IntakeBatch.unresolved_rejected > 0)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.order_by(intake.IntakeBatch.id.desc())
                      .offset(max(0, offset)).limit(max(1, min(limit, 1000)))).all()
    return {"rows": [intake.metadata(batch) for batch in rows], "total": total,
            "retention": intake.retention(db), "quality": intake.quality(db, sn[:50])}


@router.get("/{batch_id}")
def get_batch(batch_id: int, offset: int = 0, limit: int = 200,
              db: Session = Depends(get_db), _=Depends(_read_permission)):
    original = _batch(db, batch_id)
    lines = json.loads(original.diagnostics)
    children = db.scalars(select(intake.IntakeBatch).where(intake.IntakeBatch.replay_of == batch_id)
                         .order_by(intake.IntakeBatch.id.desc()).limit(100)).all()
    start, size = max(0, offset), max(1, min(limit, 5000))
    return {**intake.metadata(original), "line_results": lines[start:start + size],
            "line_total": len(lines), "offset": start, "limit": size,
            "replay_history": [intake.metadata(batch) for batch in children]}


@router.get("/{batch_id}/raw")
def raw_batch(batch_id: int, request: Request, db: Session = Depends(get_db),
              _=Depends(require("system.admin"))):
    original = _batch(db, batch_id)
    audit(db, request, "intake.raw_view", str(batch_id), "Original attendance-only ATTLOG bytes")
    db.commit()
    return Response(original.raw, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="attendance_upload_{batch_id}.txt"',
                             "X-Content-Type-Options": "nosniff"})


@router.post("/{batch_id}/replay")
async def replay_batch(batch_id: int, request: Request, db: Session = Depends(get_db),
                       _=Depends(require("system.admin"))):
    # Bound the request before JSON decoding instead of retaining a giant edited
    # textarea in FastAPI's dependency/body layer. Original replay accepts no body.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > intake.MAX_PAYLOAD_BYTES * 2:
            raise HTTPException(413, "edited attendance upload exceeds the request limit")
    try:
        data = json.loads(body) if body else {}
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "invalid replay JSON") from exc
    if not isinstance(data, dict) or set(data) - {"corrected_body"}:
        raise HTTPException(422, "only corrected_body is accepted")
    corrected = data.get("corrected_body")
    if "corrected_body" in data and not isinstance(corrected, str):
        raise HTTPException(422, "corrected_body must be a string")
    if corrected is not None and not corrected.strip():
        raise HTTPException(422, "corrected_body must contain attendance lines")
    return await run_in_threadpool(_replay_commit, db, request, batch_id, corrected)


def _replay_commit(db: Session, request: Request, batch_id: int, corrected: str | None):
    original = _batch(db, batch_id)
    try:
        result = intake.replay(db, original, corrected.encode("utf-8") if corrected is not None else None)
        audit(db, request, "intake.replay", str(batch_id),
              json.dumps({"replay_id": result.id, "corrected": corrected is not None,
                          "original_sha256": original.payload_sha256, "replay_sha256": result.payload_sha256,
                          "accepted": result.accepted, "duplicates": result.duplicates,
                          "rejected": result.rejected, "device_communication": False}))
        db.commit()
    except (intake.IntakeLimitError, intake.IntakePayloadError) as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from exc
    return intake.metadata(result)
