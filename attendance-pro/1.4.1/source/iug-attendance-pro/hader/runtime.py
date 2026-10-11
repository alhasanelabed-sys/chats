"""Restarting the server in place when its address or ports are changed in the program.

The page asks for a restart; every listening server is told to stop, run.py reloads the
settings and opens the new address/ports in the same process. If the new web address or
port cannot be opened, the previous ones are written back and used, and the reason is kept
for the settings page to show.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import fields

from .config import WINDOWS, load_settings, read_network, settings, write_network

log = logging.getLogger("hader.runtime")

RESTART = threading.Event()
STOP = threading.Event()      # the Service Manager asked the server to stop
SERVERS: list = []            # uvicorn servers of the web and portal ports (set by run.py)
LAST = {"error": "", "previous": None}   # outcome of the last change, shown on the settings page


def request_restart(delay: float = 0.8) -> None:
    """Stop every listening server shortly (so the answer reaches the browser first)."""
    def go():
        from .adms import ports
        RESTART.set()
        for srv in [*SERVERS, *ports.SERVERS.values()]:
            srv.should_exit = True
    threading.Timer(delay, go).start()


def request_stop(delay: float = 0.5) -> None:
    def go():
        STOP.set()
        request_restart(0)
    threading.Timer(delay, go).start()


def control_token() -> str:
    """Proof that a request comes from someone who can read this PC's data folder (the Service
    Manager on the server itself): an HMAC of the server's secret key."""
    import hashlib
    import hmac
    return hmac.new(settings.secret_key.encode(), b"hader-control", hashlib.sha256).hexdigest()


def pid_file():
    return settings.data_dir / "hader.pid"


def reload_settings() -> None:
    new = load_settings()
    for f in fields(settings):
        setattr(settings, f.name, getattr(new, f.name))


def remember_previous() -> None:
    LAST["previous"] = {"host": settings.host, "web_port": settings.web_port, "portal_port": settings.portal_port,
                        "adms_ports": ",".join(str(p) for p in settings.adms_ports)}
    LAST["error"] = ""


def restore_previous(error: str) -> None:
    """The new address/port could not be opened: put the previous ones back."""
    prev = LAST.get("previous")
    LAST["error"] = error
    if prev:
        write_network(settings.data_dir, prev)
    else:
        try:
            os.remove(settings.data_dir / "network.ini")
        except OSError:
            pass
    LAST["previous"] = None
    reload_settings()


def applied() -> None:
    LAST["previous"] = None


def current() -> dict:
    saved = read_network(settings.data_dir)
    return {"host": settings.host, "web_port": settings.web_port, "portal_port": settings.portal_port,
            "adms_ports": settings.adms_ports, "from_program": bool(saved), "error": LAST.get("error", ""),
            "fixed": [k for k in ("host", "web_port", "portal_port", "adms_ports") if os.getenv("HADER_" + k.upper())]}


def firewall() -> None:
    """On Windows, when running with administrator rights (the autostart task does), open the
    current ports in Windows Firewall so a change of port in the program just works."""
    if not WINDOWS:
        return
    ports = sorted({settings.web_port, *([settings.portal_port] if settings.portal_port else []), *settings.adms_ports})
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", "name=Hader"],
                       capture_output=True, timeout=15, creationflags=flags)
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", "name=Hader", "dir=in", "action=allow",
                            "protocol=TCP", "localport=" + ",".join(map(str, ports))],
                           capture_output=True, timeout=15, creationflags=flags)
        if r.returncode:
            log.info("firewall not changed (run as administrator to open ports %s automatically)", ports)
    except (OSError, subprocess.SubprocessError):
        pass
