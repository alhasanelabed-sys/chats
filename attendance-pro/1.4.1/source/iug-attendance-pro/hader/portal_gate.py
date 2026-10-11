"""The employee-portal port: only the portal answers there.

Hader can listen on a second port (``portal_port``, 8091 by default) that serves the employee
portal and nothing else - no admin pages, no admin API, no terminal (ADMS) endpoints. That is
the port to publish on the internet (router port-forward or a tunnel), so employees can open
the portal from home while the rest of the system stays inside the network.
"""
from __future__ import annotations

import re

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from .config import settings

PORTAL_PATH = re.compile(r"^/(?:me|me/sw\.js|static/me/[\w.\-/]+|api/me(?:/[\w.\-/]*)?|favicon\.ico)$")
TRUSTED_PROXIES = {"127.0.0.1", "::1", "localhost"}
BLOCKED = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>حاضر</title><body style="font-family:Segoe UI,Tahoma,sans-serif;background:#f2f4f7;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="background:#fff;border-radius:20px;padding:28px 32px;box-shadow:0 8px 24px rgba(0,0,0,.08);max-width:420px;text-align:center">
<div style="font-size:40px">🔒</div><h2 style="margin:8px 0">واجهة الإدارة غير متاحة من هذا الجهاز</h2>
<p style="color:#5b6b7b">يحددها مدير النظام. للموظفين: افتح بوابة الموظف.</p>
<p style="color:#5b6b7b;direction:ltr">The admin interface is not available from this device.</p>
<a href="/me" style="display:inline-block;margin-top:8px;background:#0e8f86;color:#fff;border-radius:999px;padding:10px 22px;text-decoration:none">بوابة الموظف</a></div>"""
LISTENING = False   # set by run.py once the portal port is open

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
}


def on_portal_port(request: Request) -> bool:
    server = request.scope.get("server") or (None, None)
    return bool(settings.portal_port) and server[1] == settings.portal_port


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    return peer in TRUSTED_PROXIES and request.headers.get("x-forwarded-proto", "").lower() == "https"


def client_ip(request: Request) -> str:
    """The employee's address, also behind a tunnel / reverse proxy running on this PC."""
    peer = request.client.host if request.client else ""
    if peer in TRUSTED_PROXIES:
        fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if fwd.strip():
            return fwd.strip()
    return peer


ALWAYS_OPEN = re.compile(r"^/(?:iclock/|api/ping$)")   # terminals and the restart check
_ADMIN = {"at": 0.0, "value": None}


def _admin_rule():
    """The admin-access setting, read at most every 5 seconds."""
    import time
    if time.monotonic() - _ADMIN["at"] > 5:
        from . import store
        from .db import session_scope
        with session_scope() as db:
            _ADMIN["value"] = (store.get(db, "security.admin_from") or "").strip()
        _ADMIN["at"] = time.monotonic()
    return _ADMIN["value"]


def admin_allowed(ip: str, rule: str) -> bool:
    """rule: "" = anyone, "local" = this PC only, else IPs / networks (10.0.0.5, 10.0.1.0/24, 10.0.0.5-10.0.0.9)."""
    import ipaddress
    if not rule:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback or ip in lan_addresses():   # the server PC itself can never be locked out
        return True
    if rule == "local":
        return False
    for part in rule.replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        try:
            if "-" in part:
                a, b = (ipaddress.ip_address(x.strip()) for x in part.split("-", 1))
                if a <= addr <= b:
                    return True
            elif part and addr in ipaddress.ip_network(part, strict=False):
                return True
        except ValueError:
            continue
    return False


async def gate(request: Request, call_next) -> Response:
    path = request.url.path
    portal_port = on_portal_port(request)
    if not portal_port and not PORTAL_PATH.match(path) and not ALWAYS_OPEN.match(path):
        rule = _admin_rule()
        if rule and not admin_allowed(request.client.host if request.client else "", rule):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "واجهة الإدارة غير متاحة من هذا الجهاز / The admin interface is not "
                                               "available from this device"}, status_code=403)
            return HTMLResponse(BLOCKED, status_code=403)
    if portal_port:
        if path in ("/", ""):
            return RedirectResponse("/me")
        if path == "/robots.txt":
            return PlainTextResponse("User-agent: *\nDisallow: /\n")
        if not PORTAL_PATH.match(path):
            return PlainTextResponse("Not found", status_code=404)
    resp = await call_next(request)
    if portal_port or PORTAL_PATH.match(path):
        for k, v in HEADERS.items():
            resp.headers.setdefault(k, v)
        if is_https(request):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def lan_addresses() -> list[str]:
    """This PC's IPv4 addresses on the local network(s)."""
    import socket
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # no packet is sent: picks the main interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254."))]
