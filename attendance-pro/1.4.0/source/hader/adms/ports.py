"""How each terminal port is being served — kept up to date by run.py.

mode:
  own       this program listens on the port
  waiting   another program (another attendance server) holds it; run.py retries every few seconds
            and takes the port as soon as it is released — no restart needed
  released  given back on purpose (button) so another program can use it; not retaken
            until "take" is pressed

The *device port* is the one written in the terminals (90). Owning it = full mode
(ADMS: the terminals talk to this program and every function works). Not owning it =
read-only mode (terminals are only read over 4370 and nothing is written to them).
"""
from __future__ import annotations

PORTS: dict[int, dict] = {}
DEVICE_PORT = 90
SERVERS: dict[int, object] = {}   # port -> uvicorn.Server serving it (for release)
RELEASED: set[int] = set()


def set_mode(port: int, mode: str, detail: str = "") -> None:
    PORTS[port] = {"port": port, "mode": mode, "detail": detail}


def snapshot() -> list[dict]:
    return [PORTS[p] for p in sorted(PORTS)]


def device_port() -> int:
    """90 when configured (the port written in the terminals), else the first ADMS port."""
    from ..config import settings
    ports = [p for p in settings.adms_ports if p != settings.web_port]
    return DEVICE_PORT if DEVICE_PORT in ports or not ports else ports[0]


def full_mode() -> bool:
    """True when this program owns the port the terminals push to (or runs without run.py)."""
    if not PORTS:
        return True
    return PORTS.get(device_port(), {}).get("mode") == "own"


def release(port: int) -> bool:
    RELEASED.add(port)
    srv = SERVERS.get(port)
    if srv is not None:
        srv.should_exit = True
        return True
    return False


def take(port: int) -> None:
    RELEASED.discard(port)
