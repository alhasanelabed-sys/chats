"""Database schema, grouped by module.

Personnel: Department, Position, Area, Employee, BioTemplate, BioPhoto
Device:    Device, DeviceCommand, Transaction, DeviceOpLog, DeviceErrorLog
Attendance: TimeTable, Shift, ShiftDetail, Schedule, DeptSchedule, TempSchedule,
            Holiday, LeaveType, Leave, ManualLog, Overtime
System:    User, Role, AuditLog, Setting
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, LargeBinary, String, Table, Text, Time,
                        UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, now

# --------------------------------------------------------------------------
# Personnel
# --------------------------------------------------------------------------

employee_area = Table(
    "employee_area", Base.metadata,
    Column("employee_id", ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True),
    Column("area_id", ForeignKey("area.id", ondelete="CASCADE"), primary_key=True),
)


class Department(Base):
    __tablename__ = "department"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str | None] = mapped_column(String(120), default="")

    @property
    def label(self) -> str:
        from .i18n import pick
        return pick(self.name, self.name_en)

    parent_id: Mapped[int | None] = mapped_column(ForeignKey("department.id", ondelete="SET NULL"))


class Position(Base):
    __tablename__ = "position"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str | None] = mapped_column(String(120), default="")

    @property
    def label(self) -> str:
        from .i18n import pick
        return pick(self.name, self.name_en)

    parent_id: Mapped[int | None] = mapped_column(ForeignKey("position.id", ondelete="SET NULL"))


class Area(Base):
    """A group of devices. Employees assigned to an area are synced to every
    device in it (core distribution model)."""
    __tablename__ = "area"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str | None] = mapped_column(String(120), default="")

    @property
    def label(self) -> str:
        from .i18n import pick
        return pick(self.name, self.name_en)

    parent_id: Mapped[int | None] = mapped_column(ForeignKey("area.id", ondelete="SET NULL"))


class Employee(Base):
    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(primary_key=True)
    emp_code: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # device PIN
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    name_en: Mapped[str | None] = mapped_column(String(160), default="")  # English full name (optional)
    # Employee portal (self-service)
    portal_enabled: Mapped[bool | None] = mapped_column(Boolean, default=False)
    portal_hash: Mapped[str | None] = mapped_column(String(200), default="")
    portal_must_change: Mapped[bool | None] = mapped_column(Boolean, default=True)
    portal_last_login: Mapped[datetime | None] = mapped_column(DateTime)
    portal_lang: Mapped[str | None] = mapped_column(String(2), default="ar")
    gender: Mapped[str] = mapped_column(String(1), default="")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("department.id", ondelete="SET NULL"), index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("position.id", ondelete="SET NULL"))
    hire_date: Mapped[date | None] = mapped_column(Date)
    birthday: Mapped[date | None] = mapped_column(Date)
    national_id: Mapped[str] = mapped_column(String(50), default="")
    mobile: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(String(250), default="")
    emp_type: Mapped[str] = mapped_column(String(20), default="permanent")
    # Device-side fields
    card_no: Mapped[str] = mapped_column(String(30), default="")
    dev_password: Mapped[str] = mapped_column(String(30), default="")
    dev_privilege: Mapped[int] = mapped_column(Integer, default=0)  # 0 user, 14 super admin
    verify_mode: Mapped[int] = mapped_column(Integer, default=-1)   # -1 = device default
    enable_att: Mapped[bool] = mapped_column(Boolean, default=True)
    photo: Mapped[str] = mapped_column(Text, default="")            # base64 JPEG (USERPIC)
    # Resignation
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)  # active / resigned
    resign_date: Mapped[date | None] = mapped_column(Date)
    resign_type: Mapped[str] = mapped_column(String(30), default="")
    resign_reason: Mapped[str] = mapped_column(String(250), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    areas: Mapped[list[Area]] = relationship(secondary=employee_area, lazy="selectin")
    department: Mapped[Department | None] = relationship(lazy="joined")
    position: Mapped[Position | None] = relationship(lazy="joined")

    @property
    def full_name(self) -> str:
        """The name as typed (what the terminals get)."""
        return (f"{self.first_name} {self.last_name}").strip()

    @property
    def display_name(self) -> str:
        """The name in the language of the current page (English name or transliteration)."""
        from .i18n import pick
        return pick(self.full_name, self.name_en)


# BIODATA "Type" values of the PUSH protocol.
BIO_TYPES = {
    0: "general", 1: "fingerprint", 2: "face", 3: "voice", 4: "iris", 5: "retina",
    6: "palmprint", 7: "fingervein", 8: "palm", 9: "vl_face",
}


class BioTemplate(Base):
    __tablename__ = "bio_template"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    bio_type: Mapped[int] = mapped_column(Integer)          # see BIO_TYPES
    bio_no: Mapped[int] = mapped_column(Integer, default=0)  # finger id / template number
    bio_index: Mapped[int] = mapped_column(Integer, default=0)
    valid: Mapped[int] = mapped_column(Integer, default=1)
    duress: Mapped[int] = mapped_column(Integer, default=0)
    major_ver: Mapped[str] = mapped_column(String(10), default="")
    minor_ver: Mapped[str] = mapped_column(String(10), default="")
    bio_format: Mapped[int] = mapped_column(Integer, default=0)
    template: Mapped[str] = mapped_column(Text)
    source_sn: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("employee_id", "bio_type", "bio_no", "bio_index", "major_ver",
                                       name="uq_bio_template"),)


class BioPhoto(Base):
    """Enrollment photo (e.g. visible-light face) that a device can re-extract."""
    __tablename__ = "bio_photo"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    bio_type: Mapped[int] = mapped_column(Integer, default=9)
    content: Mapped[str] = mapped_column(Text)  # base64 JPEG
    source_sn: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("employee_id", "bio_type", name="uq_bio_photo"),)


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------

class Device(Base):
    __tablename__ = "device"
    id: Mapped[int] = mapped_column(primary_key=True)
    sn: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    alias: Mapped[str] = mapped_column(String(100), default="")
    alias_en: Mapped[str | None] = mapped_column(String(100), default="")
    area_id: Mapped[int | None] = mapped_column(ForeignKey("area.id", ondelete="SET NULL"))
    ip: Mapped[str] = mapped_column(String(50), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_attendance: Mapped[bool] = mapped_column(Boolean, default=True)   # punches count for T&A
    is_registration: Mapped[bool] = mapped_column(Boolean, default=False)  # enrollment device
    time_zone: Mapped[int | None] = mapped_column(Integer)                # hours; None = system setting
    heartbeat: Mapped[int] = mapped_column(Integer, default=10)           # Delay=
    trans_interval: Mapped[int] = mapped_column(Integer, default=1)       # TransInterval=
    trans_times: Mapped[str] = mapped_column(String(100), default="00:00;14:05")
    realtime: Mapped[bool] = mapped_column(Boolean, default=True)
    comm_key: Mapped[str] = mapped_column(String(20), default="0")        # for TCP pull
    tcp_port: Mapped[int] = mapped_column(Integer, default=4370)
    # Read punches/users directly from the terminal over TCP 4370 on a schedule (works while
    # the terminal keeps pushing to another server).
    tcp_poll: Mapped[bool | None] = mapped_column(Boolean, default=False)
    # Reported by the terminal
    model: Mapped[str] = mapped_column(String(100), default="")
    firmware: Mapped[str] = mapped_column(String(100), default="")
    push_ver: Mapped[str] = mapped_column(String(30), default="")
    platform: Mapped[str] = mapped_column(String(60), default="")
    mac: Mapped[str] = mapped_column(String(30), default="")
    fp_alg: Mapped[str] = mapped_column(String(20), default="")
    face_alg: Mapped[str] = mapped_column(String(20), default="")
    user_count: Mapped[int] = mapped_column(Integer, default=0)
    fp_count: Mapped[int] = mapped_column(Integer, default=0)
    face_count: Mapped[int] = mapped_column(Integer, default=0)
    palm_count: Mapped[int] = mapped_column(Integer, default=0)
    att_count: Mapped[int] = mapped_column(Integer, default=0)
    options: Mapped[str] = mapped_column(Text, default="{}")  # JSON of device options/capabilities
    # Upload stamps echoed back in the option block
    att_stamp: Mapped[str] = mapped_column(String(30), default="0")
    op_stamp: Mapped[str] = mapped_column(String(30), default="0")
    photo_stamp: Mapped[str] = mapped_column(String(30), default="0")
    last_activity: Mapped[datetime | None] = mapped_column(DateTime)
    last_init: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime)  # last time the device uploaded data
    # "tcp" = found on the network and linked directly over port 4370;
    # cleared as soon as the terminal pushes to this server over ADMS.
    managed_by: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    area: Mapped[Area | None] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        from .i18n import pick
        return pick(self.alias or self.sn, self.alias_en)


class DeviceCommand(Base):
    __tablename__ = "device_command"
    id: Mapped[int] = mapped_column(primary_key=True)  # doubles as the C:<id>: in the protocol
    device_sn: Mapped[str] = mapped_column(String(50), index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(40))  # fast de-duplication of queued commands
    title: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending/sent/done/failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    return_code: Mapped[str] = mapped_column(String(20), default="")
    result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_cmd_dev_status", "device_sn", "status"),
                      Index("ix_cmd_dedupe", "device_sn", "content_hash"))


PUNCH_STATES = {0: "check_in", 1: "check_out", 2: "break_out", 3: "break_in",
                4: "ot_in", 5: "ot_out", 255: "undefined"}


class Transaction(Base):
    """A punch (ATTLOG record)."""
    __tablename__ = "transaction"
    id: Mapped[int] = mapped_column(primary_key=True)
    emp_code: Mapped[str] = mapped_column(String(30), index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employee.id", ondelete="SET NULL"), index=True)
    punch_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    punch_state: Mapped[int] = mapped_column(Integer, default=0)
    verify_type: Mapped[int] = mapped_column(Integer, default=0)
    work_code: Mapped[str] = mapped_column(String(20), default="")
    device_sn: Mapped[str] = mapped_column(String(50), default="", index=True)
    temperature: Mapped[float | None] = mapped_column(Float)
    mask: Mapped[int | None] = mapped_column(Integer)
    photo: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(10), default="device")  # device / tcp / import / manual
    upload_time: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("emp_code", "punch_time", "device_sn", name="uq_transaction"),
                      Index("ix_transaction_employee_time", "employee_id", "punch_time"),
                      Index("ix_transaction_device_time", "device_sn", "punch_time"))


class DeviceOpLog(Base):
    __tablename__ = "device_oplog"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_sn: Mapped[str] = mapped_column(String(50), index=True)
    op_code: Mapped[int] = mapped_column(Integer, default=0)
    admin: Mapped[str] = mapped_column(String(30), default="")
    op_time: Mapped[datetime | None] = mapped_column(DateTime)
    obj1: Mapped[str] = mapped_column(String(50), default="")
    obj2: Mapped[str] = mapped_column(String(50), default="")
    obj3: Mapped[str] = mapped_column(String(50), default="")
    obj4: Mapped[str] = mapped_column(String(50), default="")
    upload_time: Mapped[datetime] = mapped_column(DateTime, default=now)


class DeviceErrorLog(Base):
    __tablename__ = "device_errorlog"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_sn: Mapped[str] = mapped_column(String(50), index=True)
    err_code: Mapped[str] = mapped_column(String(20), default="")
    err_msg: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[str] = mapped_column(Text, default="")
    upload_time: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------------------------------
# Attendance rules
# --------------------------------------------------------------------------

class TimeTable(Base):
    __tablename__ = "timetable"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="normal")  # normal / flexible
    check_in: Mapped[time] = mapped_column(Time, default=time(8, 0))
    check_out: Mapped[time] = mapped_column(Time, default=time(17, 0))
    # Punch windows around check-in/out (minutes)
    in_ahead: Mapped[int] = mapped_column(Integer, default=120)
    in_above: Mapped[int] = mapped_column(Integer, default=240)
    out_ahead: Mapped[int] = mapped_column(Integer, default=240)
    out_above: Mapped[int] = mapped_column(Integer, default=240)
    late_grace: Mapped[int] = mapped_column(Integer, default=0)
    early_grace: Mapped[int] = mapped_column(Integer, default=0)
    must_check_in: Mapped[bool] = mapped_column(Boolean, default=True)
    must_check_out: Mapped[bool] = mapped_column(Boolean, default=True)
    break_start: Mapped[time | None] = mapped_column(Time)
    break_end: Mapped[time | None] = mapped_column(Time)
    # Flexible timetable: minimum work minutes in the day window
    work_minutes: Mapped[int] = mapped_column(Integer, default=480)
    workday: Mapped[float] = mapped_column(Float, default=1.0)  # counts as N work days
    color: Mapped[str] = mapped_column(String(10), default="#1e88e5")


class Shift(Base):
    __tablename__ = "shift"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(60), unique=True)
    cycle_unit: Mapped[str] = mapped_column(String(5), default="week")  # day / week / month
    cycle: Mapped[int] = mapped_column(Integer, default=1)
    details: Mapped[list["ShiftDetail"]] = relationship(cascade="all, delete-orphan", lazy="selectin",
                                                        order_by="ShiftDetail.day_index")


class ShiftDetail(Base):
    __tablename__ = "shift_detail"
    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id", ondelete="CASCADE"), index=True)
    day_index: Mapped[int] = mapped_column(Integer)  # 0..(cycle*unit_days-1); week: 0 = Monday
    timetable_id: Mapped[int] = mapped_column(ForeignKey("timetable.id", ondelete="CASCADE"))


class Schedule(Base):
    """Employee shift assignment for a date range."""
    __tablename__ = "schedule"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id", ondelete="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # Remains stable when a date range is split around a temporary assignment.
    cycle_anchor: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (Index("ix_schedule_employee_dates", "employee_id", "start_date", "end_date"),)


class DeptSchedule(Base):
    """Default shift for a department (applies to employees without their own)."""
    __tablename__ = "dept_schedule"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id", ondelete="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)


class TempSchedule(Base):
    """One-day override."""
    __tablename__ = "temp_schedule"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    att_date: Mapped[date] = mapped_column(Date, index=True)
    timetable_id: Mapped[int | None] = mapped_column(ForeignKey("timetable.id", ondelete="CASCADE"))  # None = day off
    __table_args__ = (UniqueConstraint("employee_id", "att_date", "timetable_id", name="uq_temp_sched"),)


class Holiday(Base):
    __tablename__ = "holiday"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(80))
    start_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer, default=1)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("department.id", ondelete="CASCADE"))  # None = all


class LeaveType(Base):
    __tablename__ = "leave_type"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    name_en: Mapped[str | None] = mapped_column(String(120), default="")

    @property
    def label(self) -> str:
        from .i18n import pick
        return pick(self.name, self.name_en)

    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    color: Mapped[str] = mapped_column(String(10), default="#8e24aa")
    annual_days: Mapped[int | None] = mapped_column(Integer, default=0)  # yearly entitlement (0 = not tracked)
    portal: Mapped[bool | None] = mapped_column(Boolean, default=True)  # employees may request it from the portal


APPROVAL_STATES = ("pending", "approved", "rejected")


class Leave(Base):
    __tablename__ = "leave"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    leave_type_id: Mapped[int] = mapped_column(ForeignKey("leave_type.id", ondelete="RESTRICT"))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(250), default="")
    status: Mapped[str] = mapped_column(String(10), default="approved")
    approver: Mapped[str] = mapped_column(String(50), default="")
    source: Mapped[str | None] = mapped_column(String(10), default="admin")  # admin | portal
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ManualLog(Base):
    """Manual punch (forgotten punch) — counted once approved."""
    __tablename__ = "manual_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    punch_time: Mapped[datetime] = mapped_column(DateTime)
    punch_state: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(250), default="")
    status: Mapped[str] = mapped_column(String(10), default="approved")
    approver: Mapped[str] = mapped_column(String(50), default="")
    source: Mapped[str | None] = mapped_column(String(10), default="admin")  # admin | portal
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Overtime(Base):
    """Overtime application (counted once approved)."""
    __tablename__ = "overtime"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(250), default="")
    status: Mapped[str] = mapped_column(String(10), default="approved")
    approver: Mapped[str] = mapped_column(String(50), default="")
    source: Mapped[str | None] = mapped_column(String(10), default="admin")  # admin | portal
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------

class Role(Base):
    __tablename__ = "role"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("role.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(5), default="ar")
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(40), default="")
    role: Mapped[Role | None] = relationship(lazy="joined")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), default="system")
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Blob(Base):
    """Small binary storage (e.g. company logo)."""
    __tablename__ = "blob"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)


# --------------------------------------------------------------------------
# Notifications & alerts
# --------------------------------------------------------------------------

class Notification(Base):
    """In-app notification for a user (bell) or an employee (portal inbox)."""
    __tablename__ = "notification"
    id: Mapped[int] = mapped_column(primary_key=True)
    to_kind: Mapped[str] = mapped_column(String(10), index=True)   # user | employee
    to_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="")
    level: Mapped[str] = mapped_column(String(10), default="info")  # info | good | warn | bad
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class AlertLog(Base):
    """Every alert decision: what, to whom, through which channel, and how it went."""
    __tablename__ = "alert_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), index=True)       # same key = same event (no repeats)
    rule: Mapped[str] = mapped_column(String(30), default="")
    channel: Mapped[str] = mapped_column(String(15), default="")    # inapp | email | telegram | webhook
    target: Mapped[str] = mapped_column(String(160), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(10), default="sent") # sent | failed | skipped
    error: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
