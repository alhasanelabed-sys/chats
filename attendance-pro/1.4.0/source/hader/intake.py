"""Durable, bounded attendance-upload inbox and database-only replay.

The original ATTLOG bytes, outcome diagnostics, punches, and device stamp share
one transaction. A terminal receives its normal acknowledgement only after that
transaction commits. Templates, photos, passwords and OPERLOG are never archived
by this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from types import SimpleNamespace

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, case, delete, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import models as m, store
from .adms import protocol as P, sync
from .db import Base
from .terminal.protocol import valid_pin

MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_LINES = 50_000
DEFAULT_RETENTION = {"days": 30, "max_batches": 10_000, "max_bytes": 128 * 1024 * 1024}
_OTHER_TABLE = re.compile(rb"(?im)^\s*(?:USER|FP|FACE|BIODATA|BIOPHOTO|USERPIC|OPLOG)\s+")
_PRIVATE_FIELD = re.compile(rb"(?i)(?:^|[\t \r\n])(?:passwd|password|tmp|template|content)=")


class IntakeLimitError(ValueError):
    """The payload is not accepted, so the terminal must retain and retry it."""
    status = 413


class IntakePayloadError(ValueError):
    status = 422


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IntakeBatch(Base):
    __tablename__ = "intake_batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_sn: Mapped[str] = mapped_column(String(50), index=True)
    source_stamp: Mapped[str] = mapped_column(String(64), default="")
    received_at_utc: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload_bytes: Mapped[int] = mapped_column(Integer)
    storage_bytes: Mapped[int] = mapped_column(Integer)
    raw: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    diagnostics: Mapped[str] = mapped_column(Text, deferred=True)
    received: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0, index=True)
    ack_count: Mapped[int] = mapped_column(Integer, default=0)
    replay_of: Mapped[int | None] = mapped_column(ForeignKey("intake_batch.id", ondelete="CASCADE"), index=True)
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    unresolved_rejected: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_intake_source_received", "source_sn", "received_at_utc"),)


def retention(db: Session) -> dict:
    result = {}
    ranges = {"days": (1, 3650), "max_batches": (10, 100_000), "max_bytes": (1024 * 1024, 1024 ** 3)}
    for name, default in DEFAULT_RETENTION.items():
        value = store.get(db, "intake.retention_days" if name == "days" else "intake." + name, default)
        lo, hi = ranges[name]
        result[name] = max(lo, min(hi, value)) if type(value) is int else default
    return {**result, "rejected_batches_preserved": True, "replay_evidence_preserved": True}


def validate_payload(raw: bytes) -> None:
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise IntakeLimitError("ATTLOG exceeds the 4 MiB upload limit; split the device upload into smaller batches")
    # Never make the diagnostic inbox a second archive of passwords/biometrics,
    # including mislabeled device uploads. The terminal receives no success ACK.
    if _OTHER_TABLE.search(raw) or _PRIVATE_FIELD.search(raw):
        raise IntakePayloadError("ATTLOG contains non-attendance data; use the correct device table")
    if raw.count(b"\n") + raw.count(b"\r") > MAX_LINES * 2:
        raise IntakeLimitError("ATTLOG exceeds the 50000-line upload limit")


def _prunable():
    # Reviewed/replayed evidence remains available with its immutable original.
    return (IntakeBatch.rejected == 0, IntakeBatch.replay_of.is_(None), IntakeBatch.replay_count == 0)


def prune_accepted(db: Session) -> int:
    """Remove only clean, normal inbox history; never remove attendance records."""
    limits = retention(db)
    db.flush()
    old = select(IntakeBatch.id).where(*_prunable(),
                                     IntakeBatch.received_at_utc < utc_now() - timedelta(days=limits["days"]))
    removed = db.execute(delete(IntakeBatch).where(IntakeBatch.id.in_(old))).rowcount or 0
    total, size = db.execute(select(func.count(), func.coalesce(func.sum(IntakeBatch.storage_bytes), 0))
                            .where(*_prunable())).one()
    if total <= limits["max_batches"] and size <= limits["max_bytes"]:
        return removed
    # The newly received batch is last and individually smaller than the minimum
    # budget. It always survives the commit that precedes its acknowledgement.
    rows = db.execute(select(IntakeBatch.id, IntakeBatch.storage_bytes).where(*_prunable())
                      .order_by(IntakeBatch.id)).all()
    victims = []
    for batch_id, batch_size in rows[:-1]:
        if total <= limits["max_batches"] and size <= limits["max_bytes"]:
            break
        victims.append(batch_id)
        total -= 1
        size -= batch_size
    for start in range(0, len(victims), 500):
        removed += db.execute(delete(IntakeBatch).where(IntakeBatch.id.in_(victims[start:start + 500]))).rowcount or 0
    return removed


def ingest(db: Session, device, raw: bytes, stamp: str = "", *, replay_of: IntakeBatch | None = None) -> IntakeBatch:
    validate_payload(raw)
    lines = P.parse_attlog_detailed(P.decode_body(raw))
    if len(lines) > MAX_LINES:
        raise IntakeLimitError("ATTLOG exceeds the 50000-line upload limit")
    # Persist the exact uploaded bytes before adding punches. An insert failure
    # rolls back registration, stamps and every record; no ACK can escape it.
    batch = IntakeBatch(source_sn=device.sn, source_stamp=(stamp or "")[:64],
                        payload_sha256=hashlib.sha256(raw).hexdigest(), payload_bytes=len(raw),
                        storage_bytes=len(raw), raw=raw, diagnostics="[]", received=len(lines),
                        replay_of=replay_of.id if replay_of is not None else None,
                        ack_count=sum(line.record is not None for line in lines))
    db.add(batch)
    db.flush()
    records, diagnostics = [], []
    for line in lines:
        reason = line.reason
        if line.pin and not valid_pin(line.pin):
            reason = reason or "invalid_employee_code"
        item = {"line": line.line}
        if line.pin:
            item["emp_code"] = line.pin[:100]
        if line.record is not None:
            item["punch_time"] = line.record.time.isoformat(sep=" ")
        if reason:
            item.update(status="rejected", reason=reason)
        elif line.record is not None:
            item["status"] = "duplicate"  # upgraded below for inserted records
            records.append(line.record)
        else:
            item.update(status="rejected", reason="invalid_record")
        diagnostics.append(item)
    inserted = {(record.pin, record.time) for record in sync.save_punches(db, device, records)}
    # One record can occur several times in one payload. Exactly one line is new;
    # the remaining occurrences are duplicates even if INSERT returned that key.
    for line, item in zip(lines, diagnostics):
        if item["status"] == "duplicate" and (line.record.pin, line.record.time) in inserted:
            item["status"] = "accepted"
            inserted.remove((line.record.pin, line.record.time))
    batch.accepted = sum(item["status"] == "accepted" for item in diagnostics)
    batch.duplicates = sum(item["status"] == "duplicate" for item in diagnostics)
    batch.rejected = sum(item["status"] == "rejected" for item in diagnostics)
    batch.unresolved_rejected = batch.rejected
    batch.diagnostics = json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":"))
    batch.storage_bytes += len(batch.diagnostics.encode("utf-8"))
    if replay_of is not None:
        # Correction covers a rejected line only at its original position and,
        # when its personnel code was readable, for that same person. Replaying
        # a shorter valid body therefore cannot hide outstanding rejected data.
        replacements = {item["line"]: item for item in diagnostics}
        unresolved = 0
        for item in json.loads(replay_of.diagnostics):
            if item["status"] != "rejected":
                continue
            replacement = replacements.get(item["line"])
            if (replacement is None or replacement["status"] == "rejected"
                    or (item.get("emp_code") and item["emp_code"] != replacement.get("emp_code"))):
                unresolved += 1
        db.execute(update(IntakeBatch).where(IntakeBatch.id == replay_of.id).values(
            replay_count=IntakeBatch.replay_count + 1,
            unresolved_rejected=case((IntakeBatch.unresolved_rejected > unresolved, unresolved),
                                     else_=IntakeBatch.unresolved_rejected)))
    db.flush()
    prune_accepted(db)
    return batch


def replay(db: Session, original: IntakeBatch, raw: bytes | None = None) -> IntakeBatch:
    """Reprocess archived bytes without registering, connecting to or queuing a device."""
    existing = db.scalar(select(m.Device).where(m.Device.sn == original.source_sn))
    context = SimpleNamespace(sn=original.source_sn, area=existing.area if existing else None,
                              managed_by="tcp")
    return ingest(db, context, original.raw if raw is None else raw, original.source_stamp, replay_of=original)


def metadata(batch: IntakeBatch) -> dict:
    return {"id": batch.id, "source_sn": batch.source_sn, "source_stamp": batch.source_stamp,
            "received_at_utc": batch.received_at_utc.isoformat(timespec="seconds") + "Z",
            "payload_sha256": batch.payload_sha256, "payload_bytes": batch.payload_bytes,
            "received": batch.received, "accepted": batch.accepted, "duplicates": batch.duplicates,
            "rejected": batch.rejected, "unresolved_rejected": batch.unresolved_rejected,
            "replay_of": batch.replay_of, "replay_count": batch.replay_count, "has_raw": True,
            "direction": "server_database_replay" if batch.replay_of else "device_to_server",
            "data_types": ["attendance_punches"]}


def quality(db: Session, sn: str = "") -> list[dict]:
    statement = select(IntakeBatch.source_sn, func.count(), func.sum(IntakeBatch.received),
                       func.sum(IntakeBatch.accepted), func.sum(IntakeBatch.duplicates),
                       func.sum(IntakeBatch.rejected), func.sum(IntakeBatch.unresolved_rejected),
                       func.max(IntakeBatch.received_at_utc)).group_by(IntakeBatch.source_sn)
    if sn:
        statement = statement.where(IntakeBatch.source_sn == sn)
    keys = ("source_sn", "batches", "received", "accepted", "duplicates", "rejected", "unresolved_rejected")
    result = []
    for row in db.execute(statement.order_by(IntakeBatch.source_sn)).all():
        item = dict(zip(keys, row[:-1]))
        item["last_received_at_utc"] = row[-1].isoformat(timespec="seconds") + "Z"
        result.append(item)
    return result
