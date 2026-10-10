"""Create tables and seed the defaults a fresh installation needs."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import time

from sqlalchemy import inspect, select, text

from .db import Base, engine, session_scope
from .config import settings
from . import models as m
from . import intake, policy_history  # register versioned-policy and device-inbox tables
from .security import PERMISSIONS, hash_password


def _save_initial_password(password: str) -> None:
    """Write a fresh installation's one-time credential atomically, owner-readable."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".initial_admin_", dir=settings.data_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(password + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(filename, settings.data_dir / "initial_admin_password.txt")
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def _add_missing_columns() -> None:
    """Minimal forward migration: add columns introduced by newer versions to
    existing tables (SQLite's create_all never alters a table)."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=engine.dialect)}'
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if default is not None:
                    ddl += " DEFAULT " + (str(int(default)) if isinstance(default, bool)
                                          else repr(default) if isinstance(default, str) else str(default))
                conn.execute(text(ddl))
            for index in table.indexes:  # indexes added by newer versions
                index.create(conn, checkfirst=True)


def purge_invalid_people() -> int:
    """Compatibility name: report legacy invalid PINs without deleting any records.

    Earlier releases also accepted numbers that terminals cannot encode. Invalidity
    alone therefore cannot establish that an employee or punch is corrupt.
    """
    from . import store
    from .terminal.protocol import valid_pin
    with session_scope() as db:
        bad_emps = [(emp_id, code) for emp_id, code in db.execute(select(m.Employee.id, m.Employee.emp_code))
                    if not valid_pin(code)]
        bad_codes = {code for code in db.scalars(select(m.Transaction.emp_code).distinct()) if not valid_pin(code)}
        bad_codes.update(code for _, code in bad_emps)
        report = {"employee_count": len(bad_emps), "code_count": len(bad_codes), "retained": True,
                  "employee_ids": sorted(emp_id for emp_id, _ in bad_emps)[:100],
                  "code_sample": sorted(bad_codes)[:100], "review_required": bool(bad_codes)}
        previous = store.get(db, "data.invalid_pins")
        if report != previous:
            store.set_(db, "data.invalid_pins", report)
            if bad_codes or (previous and previous.get("review_required")):
                db.add(m.AuditLog(username="system", action="review.invalid_pins", target="employee",
                                  detail=json.dumps(report, ensure_ascii=False)[:2000]))
        return len(bad_codes)


def repair_names() -> int:
    """Names stored garbled by an earlier version (Arabic read as latin-1) are fixed."""
    from .terminal.protocol import repair_mojibake
    n = 0
    with session_scope() as db:
        for e in db.scalars(select(m.Employee)).all():
            for attr in ("first_name", "last_name"):
                v = getattr(e, attr) or ""
                fixed = repair_mojibake(v)
                if fixed != v:
                    setattr(e, attr, fixed)
                    n += 1
    return n


def split_bilingual_names() -> int:
    """'الإدارة العامة / Head Office' typed in one field becomes an Arabic and an English name."""
    from .i18n import split_bilingual
    n = 0
    with session_scope() as db:
        for model in (m.Department, m.Area, m.Position, m.LeaveType):
            for row in db.scalars(select(model)).all():
                if row.name_en:
                    continue
                ar, en = split_bilingual(row.name)
                if en:
                    row.name, row.name_en = ar, en
                    n += 1
    return n


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    purge_invalid_people()
    repair_names()
    split_bilingual_names()
    with session_scope() as db:
        if not db.scalar(select(m.User).limit(1)):
            password = secrets.token_urlsafe(18)
            _save_initial_password(password)
            db.add(m.User(username="admin", full_name="Administrator", is_superuser=True,
                          password_hash=hash_password(password), must_change_password=True))
        if not db.scalar(select(m.Role).limit(1)):
            db.add_all([
                m.Role(name="HR Manager", permissions=json.dumps(
                    [p for p in PERMISSIONS if p != "system.admin"])),
                m.Role(name="Viewer", permissions=json.dumps(
                    ["personnel.view", "device.view", "attendance.view", "reports.view"])),
            ])
        if not db.scalar(select(m.Department).limit(1)):
            db.add(m.Department(code="1", name="الإدارة العامة", name_en="Head Office"))
        if not db.scalar(select(m.Area).limit(1)):
            db.add(m.Area(code="1", name="المقر الرئيسي", name_en="HQ"))
        if not db.scalar(select(m.Position).limit(1)):
            db.add(m.Position(code="1", name="موظف", name_en="Staff"))
        if not db.scalar(select(m.LeaveType).limit(1)):
            db.add_all([
                m.LeaveType(code="AL", name="إجازة سنوية", name_en="Annual", color="#43a047"),
                m.LeaveType(code="SL", name="إجازة مرضية", name_en="Sick", color="#e53935"),
                m.LeaveType(code="UL", name="بدون راتب", name_en="Unpaid", paid=False, color="#757575"),
                m.LeaveType(code="BT", name="مهمة عمل", name_en="Business trip", color="#1e88e5"),
            ])
        if not db.scalar(select(m.TimeTable).limit(1)):
            tt = m.TimeTable(alias="Day 08:00-16:00", check_in=time(8, 0), check_out=time(16, 0),
                             late_grace=10, early_grace=5)
            db.add(tt)
            db.flush()
            shift = m.Shift(alias="Sun-Thu", cycle_unit="week", cycle=1)
            # week index 0 = Monday ... 6 = Sunday
            shift.details = [m.ShiftDetail(day_index=d, timetable_id=tt.id) for d in (6, 0, 1, 2, 3)]
            db.add(shift)
