"""Non-optional boundary for the isolated demo: no physical device or network actions."""
from __future__ import annotations

import os
import re

from starlette.responses import JSONResponse

from .security import LoginThrottle

BLOCK_MESSAGE = "بيئة تجريبية: الاتصال بالأجهزة الفعلية غير متاح / Demo environment: physical device access is disabled"
_ACCESS_THROTTLE = LoginThrottle(max_account_failures=20, max_ip_failures=20)
_DEVICE_ACTIVE = re.compile(r"^/api/devices/[^/]+/(?:action|pull|server|panel)(?:/.*)?$")
_EMPLOYEE_ACTIVE = re.compile(r"^/api/employees/[^/]+/(?:enroll|pull-bio)$")
_ALWAYS_OFF = {"tcp.write_back", "tcp.read_bio", "discovery.enabled", "alerts.enabled", "adms.auto_add"}


def is_demo() -> bool:
    return os.environ.get("HADER_DEMO_MODE") == "1"


async def gate(request, call_next):
    if not is_demo():
        return await call_next(request)
    path, method = request.url.path, request.method
    blocked = path == "/iclock" or path.startswith("/iclock/")
    blocked |= bool(_DEVICE_ACTIVE.fullmatch(path) or _EMPLOYEE_ACTIVE.fullmatch(path))
    blocked |= bool(re.fullmatch(r"/api/operations/devices/[^/]+/clock-check", path))
    blocked |= path in {"/api/operations/backup-check", "/api/alerts/test", "/api/devices/discover", "/api/devices/probe", "/api/devices/pull-everyone"}
    blocked |= method not in {"GET", "HEAD", "OPTIONS"} and path in {"/api/link-mode", "/api/system/network", "/api/system/control"}
    blocked |= method not in {"GET", "HEAD", "OPTIONS"} and (path.startswith("/api/demo-environments") or path.startswith("/api/experiments"))
    if path.endswith("/restore") and path.startswith("/api/backups/"):
        blocked = True
    if method in {"POST", "PUT", "PATCH"} and (path == "/api/settings" or path == "/api/alerts/config" or path == "/api/devices" or re.fullmatch(r"/api/devices/\d+", path)):
        try:
            data = await request.json()
        except (ValueError, TypeError):
            data = {}
        if isinstance(data, dict):
            nested = data.get("settings") if path == "/api/alerts/config" else data
            if isinstance(nested, dict):
                blocked |= any(key in nested and nested[key] is not False for key in _ALWAYS_OFF)
                blocked |= bool(nested.get("backup.offsite_path"))
            if path.startswith("/api/devices"):
                blocked |= bool(data.get("ip") or data.get("tcp_poll"))
    if blocked:
        return JSONResponse({"detail": {"code": "demo_device_access_disabled", "message": BLOCK_MESSAGE}}, status_code=409)
    response = await call_next(request)
    response.headers["X-Hader-Demo"] = "1"
    return response
