"""Immutable, effective-dated attendance policy snapshots.

Legacy installations keep their existing live-rule behavior until a policy is
explicitly dated. The first revision preserves a baseline for earlier dates.
Later ordinary edits of a versioned policy are dated today instead of rewriting
the baseline. Revisions and their before/after values share the caller's DB
transaction, so failed edits never leave partial history behind.
"""
from __future__ import annotations

import json
import math
import re
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time
from types import SimpleNamespace

from sqlalchemy import Date, DateTime, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import models as m, store
from .db import Base, now


class AttendancePolicy(Base):
    __tablename__ = "attendance_policy"
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer, default=0)
    effective_from: Mapped[date] = mapped_column(Date)
    snapshot: Mapped[str] = mapped_column(Text)
    before: Mapped[str] = mapped_column(Text, default="{}")
    actor: Mapped[str] = mapped_column(String(50), default="system")
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (Index("ix_attendance_policy_lookup", "scope", "target_id", "effective_from", "id"),)


TIME_FIELDS = {"check_in", "check_out", "break_start", "break_end"}
BOOL_FIELDS = {"must_check_in", "must_check_out"}
INT_FIELDS = {"in_ahead", "in_above", "out_ahead", "out_above", "late_grace", "early_grace", "work_minutes"}
TT_FIELDS = {c.key for c in m.TimeTable.__table__.columns} - {"id"}
RULE_FIELDS = {key for key in store.DEFAULTS if key.startswith("att.")}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def timetable_snapshot(obj) -> dict:
    values = {}
    for field in TT_FIELDS:
        value = getattr(obj, field)
        values[field] = value.isoformat() if isinstance(value, time) else value
    return values


def rules_snapshot(db: Session) -> dict:
    return {key: store.get(db, key) for key in RULE_FIELDS}


def materialize_timetable(target_id: int, values: dict):
    values = dict(values)
    for key in TIME_FIELDS:
        if values.get(key) is not None:
            values[key] = time.fromisoformat(values[key])
    return SimpleNamespace(id=target_id, **values)


def serialize_policy(row: AttendancePolicy) -> dict:
    return {"id": row.id, "scope": row.scope, "target_id": row.target_id,
            "effective_from": row.effective_from.isoformat(), "created_at": row.created_at.isoformat(),
            "actor": row.actor, "reason": row.reason, "before": json.loads(row.before),
            "snapshot": json.loads(row.snapshot), "baseline": row.effective_from == date.min}


class PolicyTimeline:
    """Load once per calculation batch; resolve without a query per employee/day."""
    def __init__(self, db: Session, overrides: list[dict] | None = None):
        self.rows = defaultdict(list)
        for row in db.scalars(select(AttendancePolicy).order_by(
                AttendancePolicy.effective_from, AttendancePolicy.id)):
            self.rows[(row.scope, row.target_id)].append(serialize_policy(row))
        for override in overrides or []:
            key = (override["scope"], override["target_id"])
            self.rows[key].append(override)
            self.rows[key].sort(key=lambda item: (item["effective_from"], item.get("id", 0)))
        self.dates = {key: [row["effective_from"] for row in rows] for key, rows in self.rows.items()}

    def at(self, scope: str, target_id: int, day: date) -> dict | None:
        key = (scope, target_id)
        dates = self.dates.get(key, [])
        index = bisect_right(dates, day.isoformat()) - 1
        return self.rows[key][index] if index >= 0 else None


def _strict_date(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("effective_from must be YYYY-MM-DD")
    result = date.fromisoformat(value)
    if not 1900 <= result.year <= 2199:
        raise ValueError("effective_from year must be 1900..2199")
    return result


def validate_rule_changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes or set(changes) - RULE_FIELDS:
        raise ValueError("choose known attendance rule fields")
    values = dict(changes)
    for key, value in values.items():
        default = store.DEFAULTS[key]
        if isinstance(default, bool):
            if type(value) is not bool:
                raise ValueError(f"{key} must be true or false")
        elif key == "att.weekend":
            if (not isinstance(value, list) or len(value) > 7 or
                    any(type(day) is not int or not 0 <= day <= 6 for day in value) or len(set(value)) != len(value)):
                raise ValueError("att.weekend must contain unique weekdays 0..6")
        elif isinstance(default, int):
            upper = 120 if key == "att.dup_punch_minutes" else 1440
            if type(value) is not int or not 0 <= value <= upper:
                raise ValueError(f"{key} must be an integer in 0..{upper}")
        else:
            allowed = {"att.no_in": {"incomplete", "absent", "late"},
                       "att.no_out": {"incomplete", "absent", "early"},
                       "att.ot_mode": {"auto", "approval", "both"}}[key]
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"invalid {key}")
    return values


def validate_timetable_changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes or set(changes) - TT_FIELDS:
        raise ValueError("choose known timetable fields")
    values = dict(changes)
    for key, value in values.items():
        if key in TIME_FIELDS:
            if value in (None, "") and key.startswith("break_"):
                values[key] = None
            else:
                if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", value):
                    raise ValueError(f"invalid {key}: use HH:MM")
                values[key] = time.fromisoformat(value).isoformat()
        elif key in BOOL_FIELDS:
            if type(value) is not bool:
                raise ValueError(f"{key} must be true or false")
        elif key in INT_FIELDS:
            minimum = 1 if key == "work_minutes" else 0
            if type(value) is not int or not minimum <= value <= 2880:
                raise ValueError(f"{key} must be an integer in {minimum}..2880")
        elif key == "workday":
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 10:
                raise ValueError("workday must be a finite number greater than 0 and at most 10")
        elif key == "kind":
            if value not in ("normal", "flexible"):
                raise ValueError("kind must be normal or flexible")
        elif key == "color":
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError("color must be #RRGGBB")
        elif key == "alias":
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60:
                raise ValueError("timetable name must contain 1..60 characters")
            values[key] = value.strip()
    return values


def prepare_revision(db: Session, data: dict, *, preview: bool = False) -> dict:
    allowed = {"scope", "target_id", "effective_from", "changes", "reason"}
    if preview:
        allowed |= {"start", "end", "employee_ids", "department_ids"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("unknown policy option")
    scope = data.get("scope")
    if scope not in ("rules", "timetable"):
        raise ValueError("scope must be rules or timetable")
    target_id = data.get("target_id", 0)
    if type(target_id) is not int or (scope == "rules" and target_id != 0) or target_id < 0:
        raise ValueError("invalid policy target")
    effective = _strict_date(data.get("effective_from"))
    reason = data.get("reason", "")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 300:
        raise ValueError("a reason of 3..300 characters is required")
    if scope == "timetable":
        timetable = db.get(m.TimeTable, target_id)
        if timetable is None:
            raise ValueError("unknown timetable")
        live = timetable_snapshot(timetable)
        changes = validate_timetable_changes(data.get("changes"))
    else:
        live = rules_snapshot(db)
        changes = validate_rule_changes(data.get("changes"))
    previous = PolicyTimeline(db).at(scope, target_id, effective)
    before = dict(previous["snapshot"] if previous else live)
    snapshot = before | changes
    if scope == "timetable" and bool(snapshot["break_start"]) != bool(snapshot["break_end"]):
        raise ValueError("break_start and break_end must be supplied together")
    return {"scope": scope, "target_id": target_id, "effective_from": effective.isoformat(),
            "before": before, "snapshot": snapshot, "reason": reason.strip(), "live": live}


def append_revision(db: Session, prepared: dict, actor: str) -> AttendancePolicy:
    scope, target = prepared["scope"], prepared["target_id"]
    exists = db.scalar(select(AttendancePolicy.id).where(
        AttendancePolicy.scope == scope, AttendancePolicy.target_id == target).limit(1))
    if exists is None:
        db.add(AttendancePolicy(scope=scope, target_id=target, effective_from=date.min,
                               snapshot=_json(prepared["live"]), before="{}", actor=actor,
                               reason="Baseline before first dated change / القواعد قبل أول تعديل مؤرخ"))
    row = AttendancePolicy(scope=scope, target_id=target,
                           effective_from=date.fromisoformat(prepared["effective_from"]),
                           snapshot=_json(prepared["snapshot"]), before=_json(prepared["before"]),
                           actor=actor[:50], reason=prepared["reason"])
    db.add(row)
    db.flush()
    # Keep ordinary settings/forms showing the policy effective today, even
    # when a new future or backdated revision was inserted.
    current = PolicyTimeline(db).at(scope, target, now().date())
    old_flag = db.info.get("policy_write")
    db.info["policy_write"] = True
    try:
        if current and scope == "rules":
            for key, value in current["snapshot"].items():
                store.set_(db, key, value)
        elif current:
            obj = db.get(m.TimeTable, target)
            values = materialize_timetable(target, current["snapshot"])
            for key in TT_FIELDS:
                setattr(obj, key, getattr(values, key))
            db.flush()
    finally:
        db.info["policy_write"] = old_flag
    return row


def capture_rule_change(db: Session, key: str, value) -> None:
    """Hook before a normal store.set_; only scopes already dated get history."""
    if db.info.get("policy_write") or key not in RULE_FIELDS:
        return
    current = PolicyTimeline(db).at("rules", 0, now().date())
    if current is None or current["snapshot"].get(key) == value:
        return
    live = dict(current["snapshot"])
    prepared = {"scope": "rules", "target_id": 0, "effective_from": now().date().isoformat(),
                "snapshot": live | {key: value}, "before": live, "live": live,
                "reason": "Current settings edit / تعديل الإعدادات الحالية"}
    # No append_revision/current synchronization: the caller is still applying
    # its mutation. Flush this row so a following field edit sees the revision.
    db.add(AttendancePolicy(scope="rules", target_id=0, effective_from=now().date(),
                           snapshot=_json(prepared["snapshot"]), before=_json(live),
                           actor=str(db.info.get("policy_actor", "system"))[:50], reason=prepared["reason"]))
    db.flush()


def capture_timetable_change(db: Session, obj, changes: dict) -> None:
    """Hook before normal CRUD applies values to an already versioned timetable."""
    if db.info.get("policy_write") or not isinstance(obj, m.TimeTable) or obj.id is None:
        return
    current = PolicyTimeline(db).at("timetable", obj.id, now().date())
    if current is None:
        return
    before = dict(current["snapshot"])
    after = dict(before)
    for key in TT_FIELDS & changes.keys():
        value = changes[key]
        after[key] = value.isoformat() if isinstance(value, time) else value
    if before != after:
        db.add(AttendancePolicy(scope="timetable", target_id=obj.id, effective_from=now().date(),
                               snapshot=_json(after), before=_json(before),
                               actor=str(db.info.get("policy_actor", "system"))[:50],
                               reason="Current timetable edit / تعديل فترة الدوام الحالية"))
        db.flush()
