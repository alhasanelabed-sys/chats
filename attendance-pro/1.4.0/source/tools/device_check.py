"""Check a terminal from the server PC.

    python tools/device_check.py 10.28.65.253
    python tools/device_check.py 10.28.65.253 --key 0 --adms-port 8081

Checks, in order:
  1. ping                      - is the device reachable on the network?
  2. TCP 4370                  - classic SDK port (direct link)
  3. device info over TCP 4370 - serial, model, firmware, users/faces/records (built-in protocol, no extra package)
  4. ADMS port on this PC      - is Hader listening where the device pushes?
  5. has the device pushed yet - looks the serial number up in this server's database
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

OK, BAD, INFO = "[ OK ]", "[FAIL]", "[ .. ]"


def ping(ip: str) -> bool:
    flag = "-n" if platform.system() == "Windows" else "-c"
    try:
        r = subprocess.run(["ping", flag, "2", ip], capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tcp_open(ip: str, port: int, timeout: float = 4) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_address_towards(ip: str) -> str:
    """The address of this PC's network card that routes to the device."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((ip, 9))  # UDP: nothing is sent
            return s.getsockname()[0]
    except OSError:
        return "?"


def device_info(ip: str, port: int, key: str) -> dict | None:
    from hader.terminal.client import TerminalClient, TerminalError
    try:
        with TerminalClient(ip, port, key, timeout=10) as term:
            info = term.device_info()
            info.update({f"server.{k}": v for k, v in term.server_settings().items()})
            info["link"] = "UDP" if term.udp else "TCP"
            return info
    except TerminalError as exc:
        print(f"{BAD} login on port {port} failed: {exc}")
        print("       (wrong Comm Key? set it with --key; Menu > Comm. > Comm Key on the device)")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip", help="device IP address, e.g. 10.28.65.253")
    ap.add_argument("--port", type=int, default=4370, help="device TCP port (default 4370)")
    ap.add_argument("--key", default="0", help="device Comm Key (default 0)")
    ap.add_argument("--adms-port", type=int, help="ADMS port the device pushes to (default: from hader.ini)")
    args = ap.parse_args()

    print(f"\nChecking device {args.ip}\n")
    reach = ping(args.ip)
    print(f"{OK if reach else BAD} ping {args.ip}" + ("" if reach else "  (ICMP may be blocked; continuing)"))

    tcp = tcp_open(args.ip, args.port)
    print(f"{OK if tcp else BAD} TCP port {args.port} on the device")
    serial = None
    if tcp:
        info = device_info(args.ip, args.port, args.key)
        if info:
            serial = info.get("SerialNumber")
            for k, v in info.items():
                print(f"       {k:13}: {v}")

    from hader.config import settings
    ports = [args.adms_port] if args.adms_port else [settings.web_port] + settings.adms_ports
    listening = [p for p in ports if tcp_open("127.0.0.1", p, 1)]
    for p in ports:
        print(f"{OK if p in listening else BAD} this PC listens on port {p}"
              + ("" if p in listening else "  (start Hader, or another program holds the port)"))
    ip_here = local_address_towards(args.ip)
    print(f"{INFO} this PC's address (as the device should see it): {ip_here}")

    try:
        from sqlalchemy import select
        from hader.db import SessionLocal
        from hader import models as m
        with SessionLocal() as db:
            q = select(m.Device)
            q = q.where(m.Device.sn == serial) if serial else q.where(m.Device.ip == args.ip)
            dev = db.scalar(q)
        if dev:
            print(f"{OK} device {dev.sn} ({dev.alias}) is registered; last contact: {dev.last_activity}")
        else:
            print(f"{BAD} the device has not pushed to Hader yet.")
            print("       On the device: Menu > COMM. > Cloud Server Setting: Server Mode ADMS,")
            print(f"       Server Address {ip_here}, Server Port {listening[0] if listening else ports[-1]}, HTTPS off, Proxy off.")
    except Exception as exc:  # noqa: BLE001
        print(f"{INFO} database not checked: {exc}")
    print()


if __name__ == "__main__":
    main()
