"""Key/value system settings with typed defaults."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

DEFAULTS: dict[str, Any] = {
    "company.name": "My Company",
    "company.name_ar": "شركتي",
    "system.language": "ar",
    "time.zone": "",
    "storage.synchronous": "NORMAL",
    "intake.retention_days": 30,
    "intake.max_batches": 10000,
    "intake.max_bytes": 134217728,
    # --- Attendance rules ---
    "att.dup_punch_minutes": 1,       # punches closer than this are one punch
    "att.no_in": "incomplete",        # incomplete | absent | late
    "att.no_in_minutes": 60,          # late minutes when no_in == late
    "att.no_out": "incomplete",       # incomplete | absent | early
    "att.no_out_minutes": 60,
    "att.late_full": True,            # past the grace -> count all minutes (else minus grace)
    "att.ot_mode": "auto",            # auto | approval | both
    "att.ot_min_minutes": 30,         # auto OT below this is ignored
    "att.ot_before_work": False,      # count early arrival as OT
    "att.dayoff_ot": True,            # work on a day off / holiday is OT
    "att.round_minutes": 0,           # round worked/OT down to a multiple of N (0 = off)
    "att.weekend": [4, 5],            # default day off for employees without a shift (0=Mon ... 4=Fri 5=Sat)
    # --- ADMS / device communication ---
    "adms.auto_add": True,            # auto-register unknown devices that connect
    "adms.default_area": 1,
    "adms.timezone": None,            # hours; None = server local offset
    "adms.sync_bio": True,            # distribute templates enrolled on one device to the area
    "adms.read_bio_on_punch": False,  # opt in to fetching missing templates/photos on a new punch
    "adms.upload_photos": False,      # terminal punch photos: not asked for and not stored
    # Direct link to terminals over port 4370 (devices with "tcp_poll" ticked)
    "tcp.full_compare": [],           # terminals whose whole log is compared on the next read
    "tcp.poll_minutes": 30,          # full re-read (safety net); counters + new data are checked every minute
    "tcp.read_bio": True,             # also read fingerprint / face templates on every scheduled read
    # Off while another server still runs the terminals: this program only reads them (that server stays the
    # master and nothing on the terminals changes). Turn on when this program takes over.
    "tcp.write_back": False,          # employees changed here are written to TCP-only terminals
    # Find terminals on the network by themselves (no need to add them)
    "discovery.enabled": True,
    "discovery.networks": "",         # e.g. 10.0.0.0/24, 10.0.1.10-10.0.1.40 ; empty = this PC's networks
    "discovery.minutes": 30,         # full network sweep; terminals talking to this PC are checked every minute
    "discovery.comm_keys": "0",       # keys tried on a new terminal, comma separated
    # --- Employee portal ---
    "portal.public_url": "",       # internet address of the portal, e.g. https://portal.example.com
    "portal.enabled": True,
    "portal.requests": True,          # employees may send leave / correction / overtime requests
    "portal.correction_days": 31,     # how far back a punch correction may go
    # --- Alerts ---
    "alerts.enabled": True,
    "alerts.rules": {},               # overrides of alerts.DEFAULT_RULES
    "alerts.language": "ar",          # for e-mail lists and the webhook
    "alerts.quiet_from": "",          # e.g. 22:00 - only in-app notifications until quiet_to
    "alerts.quiet_to": "",
    "alerts.smtp_host": "",
    "alerts.smtp_port": 587,
    "alerts.smtp_security": "starttls",  # starttls | ssl | none
    "alerts.smtp_user": "",
    "alerts.smtp_password": "",
    "alerts.smtp_from": "",
    "alerts.email_employees": False,  # also e-mail employees (they always get it in the portal)
    "alerts.telegram_token": "",
    "alerts.webhook_url": "",         # POST JSON for WhatsApp / SMS gateways
    # --- Maintenance ---
    "backup.keep": 14,
    "backup.offsite_path": "",
    "backup.verify_after": True,
    "security.admin_from": "",        # who may open the admin pages: "" anyone | "local" this PC | IPs / networks
    "backup.hour": 2,
}


def get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return row.value


def set_(db: Session, key: str, value: Any) -> None:
    if key.startswith("att."):
        from .policy_history import capture_rule_change
        capture_rule_change(db, key, value)
    row = db.get(Setting, key)
    text = json.dumps(value, ensure_ascii=False)
    if row is None:
        db.add(Setting(key=key, value=text))
    else:
        row.value = text


def all_(db: Session, prefix: str = "") -> dict[str, Any]:
    out = {k: v for k, v in DEFAULTS.items() if k.startswith(prefix)}
    for row in db.query(Setting).all():
        if row.key.startswith(prefix):
            try:
                out[row.key] = json.loads(row.value)
            except json.JSONDecodeError:
                out[row.key] = row.value
    return out
