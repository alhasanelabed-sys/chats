"""Contact-based presence and bounded, read-only repair of today's device log.

Attendance uploads are not heartbeats.  Read permission does not imply write
permission, and requesting an ADMS range does not prove every device row arrived.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from . import models as m, store
from .config import settings
from .db import now
from .events import publish_after_commit

_KEY = "_hader_day_reconciliation"
_PROCESS = uuid.uuid4().hex
_MAX_ATTEMPTS = 3
_RETRY_SECONDS = 5 * 60


def _options(device: m.Device) -> dict:
    try:
        value = json.loads(device.options or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def is_virtual_device(device: m.Device) -> bool:
    from .demo_guard import is_demo
    opts = _options(device)
    return bool(is_demo() and opts.get("synthetic") is True and opts.get("physical_device_access") is False
                and not device.ip and not device.tcp_poll and device.managed_by != "tcp")


def contact_expiry(device: m.Device) -> int:
    limit = max(settings.offline_after, (device.heartbeat or 10) * 3)
    # Minute polling needs room for scheduling and a slow peer.  Neither mode
    # nor tcp.write_back may cause a successful read to look disconnected.
    if device.managed_by == "tcp" or device.tcp_poll:
        limit = max(limit, 3 * 60)
    return limit


def is_online(device: m.Device, stamp: datetime | None = None) -> bool:
    if not device.enabled:
        return False
    if is_virtual_device(device):
        return True  # available simulated endpoint, while its isolated app runs
    if not device.last_activity:
        return False
    age = ((stamp or now()) - device.last_activity).total_seconds()
    return -30 <= age <= contact_expiry(device)


def touch(db: Session, device: m.Device, stamp: datetime | None = None) -> None:
    """Record a verified device contact and refresh open pages after commit."""
    if not device.enabled:
        return
    stamp = stamp or now()
    device._hader_reconnected = not is_online(device, stamp)
    device.last_activity = stamp
    publish_after_commit(db, "device", sn=device.sn, state="online", last_contact=stamp.isoformat(sep=" "))


def _save(device: m.Device, state: dict) -> None:
    opts = _options(device)
    opts[_KEY] = state
    device.options = json.dumps(opts, ensure_ascii=False, separators=(",", ":"))


def reconciliation(device: m.Device, stamp: datetime | None = None) -> dict:
    stamp = stamp or now()
    state = _options(device).get(_KEY)
    if not isinstance(state, dict) or state.get("date") != stamp.date().isoformat():
        state = {"date": stamp.date().isoformat(), "status": "not_requested", "attempts": 0,
                 "start": datetime.combine(stamp.date(), time.min).isoformat(sep=" "), "end": stamp.isoformat(sep=" ")}
    return {key: value for key, value in state.items() if key not in {"process", "command_id"}}


def connection_details(db: Session, device: m.Device) -> dict:
    stamp = now()
    virtual = is_virtual_device(device)
    direct = device.managed_by == "tcp" or bool(device.tcp_poll)
    age = max(0, int((stamp - device.last_activity).total_seconds())) if device.last_activity else None
    code = ("disabled" if not device.enabled else "virtual_online" if virtual else
            "no_contact" if age is None else "expired" if not is_online(device, stamp) else
            "direct_read" if direct else "adms_heartbeat")
    return {"last_contact": device.last_activity.isoformat(sep=" ") if device.last_activity else None,
            "last_upload": device.last_sync.isoformat(sep=" ") if device.last_sync else None,
            "connection_mode": "virtual" if virtual else "direct" if direct else "adms",
            "read_only": virtual or not bool(store.get(db, "tcp.write_back")),
            "virtual_device": virtual, "is_virtual": virtual,
            "status_detail": {"code": code, "age_seconds": age, "expires_after_seconds": contact_expiry(device)},
            "day_reconciliation": {"date": stamp.date().isoformat(), "status": "simulated", "transport": "virtual"}
                                  if virtual else reconciliation(device, stamp)}


def request_today(db: Session, device: m.Device) -> None:
    """Request retained pre-contact punches at first contact/day/reconnect.

    The query is an allowed DATA QUERY in read-only mode.  Requests are bounded
    to three per calendar day and deduplicated while one is awaiting a reply.
    Unsupported firmware leaves an explicit failed state instead of claiming
    complete history.  Device log stamps and records are never reset/deleted.
    """
    if not device.enabled or not device.is_attendance or is_virtual_device(device):
        return
    from .adms import commands as C, sync
    stamp = now().replace(microsecond=0)
    opts = _options(device)
    state = opts.get(_KEY)
    if not isinstance(state, dict) or state.get("date") != stamp.date().isoformat():
        state = {"date": stamp.date().isoformat(), "attempts": 0}
    attempts = int(state.get("attempts", 0))
    command = db.get(m.DeviceCommand, state["command_id"]) if state.get("command_id") else None
    outstanding = command is not None and command.status in {"pending", "sent"}
    requested = datetime.fromisoformat(state["requested_at"]) if state.get("requested_at") else None
    timed_out = bool(requested and (stamp - requested).total_seconds() >= _RETRY_SECONDS)
    need = state.get("process") != _PROCESS or getattr(device, "_hader_reconnected", False)
    need |= bool(command is not None and command.status == "failed")
    if outstanding and not timed_out:
        return
    if not need and not (outstanding and timed_out):
        return
    if attempts >= _MAX_ATTEMPTS:
        if outstanding and timed_out:
            command.status, command.result = "failed", "history query retry budget exhausted"
        if state.get("status") not in {"read", "failed"}:
            state.update(status="failed", error="No completed history response after three requests")
            _save(device, state)
        return
    if outstanding and timed_out:
        command.status, command.result = "failed", "history query timed out; bounded read-only retry"
    start = datetime.combine(stamp.date(), time.min)
    command = sync.queue(db, device.sn, C.query_attlog(start, stamp), "Read today's retained attendance: device to server")
    if command is None:
        return
    state.update(status="requested", start=start.isoformat(sep=" "), end=stamp.isoformat(sep=" "),
                 requested_at=stamp.isoformat(sep=" "), checked_at=None, attempts=attempts + 1,
                 command_id=command.id, process=_PROCESS, transport="adms", error="")
    _save(device, state)


def attendance_received(device: m.Device) -> None:
    state = _options(device).get(_KEY)
    if isinstance(state, dict) and state.get("date") == now().date().isoformat() and state.get("status") == "requested":
        state.update(status="receiving", received_at=now().isoformat(sep=" "))
        _save(device, state)


def command_result(device: m.Device, command: m.DeviceCommand) -> None:
    state = _options(device).get(_KEY)
    if isinstance(state, dict) and state.get("command_id") == command.id:
        # A successful ACK confirms execution; firmware may still upload chunks
        # later.  Do not confuse it with proof of a full read.
        state.update(status="receiving" if command.status == "done" else "failed",
                     acknowledged_at=now().isoformat(sep=" "),
                     error="" if command.status == "done" else "Device rejected the read-only attendance query")
        _save(device, state)


def full_read(device: m.Device, records: int, new: int, *, complete: bool = True,
              raw_records: int | None = None, rejected_records: int = 0,
              truncated: bool = False, clean: bool | None = None) -> None:
    stamp = now()
    _save(device, {"date": stamp.date().isoformat(), "status": "read" if complete else "partial", "attempts": 0,
                   "start": datetime.combine(stamp.date(), time.min).isoformat(sep=" "), "end": stamp.isoformat(sep=" "),
                   "checked_at": stamp.isoformat(sep=" "), "transport": "direct", "read_records": records,
                   "new_records": new, "raw_records": records if raw_records is None else raw_records,
                   "rejected_records": rejected_records, "process": _PROCESS,
                   "transfer_truncated": truncated, "transfer_clean": clean,
                   "error": "" if complete else "Terminal attendance transfer is incomplete or contains decoding issues; diagnostic data retained"})
