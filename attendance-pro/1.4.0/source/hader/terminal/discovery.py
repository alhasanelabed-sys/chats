"""Find terminals on the local network without typing their addresses.

Every terminal answers a ``CMD_CONNECT`` on UDP 4370 (and accepts TCP 4370),
whatever its Cloud Server/ADMS setting. One UDP socket sends the probe to every
address of the subnet at once and collects the answers; addresses that did not
answer over UDP get a quick TCP check. A full scan of a /24 takes about 2-3 s.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import select
import subprocess
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from . import protocol as P

log = logging.getLogger("hader.terminal")


def local_ipv4() -> list[str]:
    """IPv4 addresses of this computer (without loopback)."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 9))
            found.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    return sorted(ip for ip in found if not ip.startswith(("127.", "169.254.", "0.")))


def default_networks() -> list[str]:
    return sorted({str(ipaddress.ip_network(f"{ip}/24", strict=False)) for ip in local_ipv4()})


def parse_networks(text: str) -> list[ipaddress.IPv4Network]:
    """``10.0.0.0/24, 10.0.1.5, 192.168.1.10-192.168.1.40`` -> networks (ranges as /32s)."""
    out: list[ipaddress.IPv4Network] = []
    for part in (text or "").replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (x.strip() for x in part.split("-", 1))
            start = ipaddress.IPv4Address(a)
            end = ipaddress.IPv4Address(b) if "." in b else ipaddress.IPv4Address(a.rsplit(".", 1)[0] + "." + b)
            out.extend(ipaddress.summarize_address_range(start, end))
        else:
            out.append(ipaddress.ip_network(part if "/" in part else part + "/32", strict=False))
    return out


def _hosts(networks) -> list[str]:
    ips: list[str] = []
    for net in networks:
        if net.num_addresses > 4096:  # refuse to sweep a /16 by accident
            raise ValueError(f"{net} is too large (max /20)")
        ips.extend(str(h) for h in (net.hosts() if net.num_addresses > 2 else net))
    return list(dict.fromkeys(ips))


def udp_sweep(ips: list[str], port: int = 4370, wait: float = 1.5) -> dict[str, int]:
    """-> {ip: reply command} for every address that answered the UDP probe."""
    probe = P.build_packet(P.CMD_CONNECT, 0, P.USHRT_MAX - 1)
    answered: dict[str, int] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        for ip in ips:
            try:
                sock.sendto(probe, (ip, port))
            except OSError:
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            ready, _, _ = select.select([sock], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            try:
                data, (ip, _p) = sock.recvfrom(2048)
            except OSError:
                continue
            try:
                pkt = P.parse_packet(data)
            except ValueError:
                continue
            if pkt.command in (P.CMD_ACK_OK, P.CMD_ACK_UNAUTH):
                answered[ip] = pkt.command
                # close the UDP session we just opened
                try:
                    sock.sendto(P.build_packet(P.CMD_EXIT, pkt.session, 0), (ip, port))
                except OSError:
                    pass
    finally:
        sock.close()
    return answered


def tcp_open(ip: str, port: int = 4370, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan(networks: str | list | None = None, port: int = 4370, tcp_fallback: bool = True) -> list[str]:
    """Addresses where a terminal answers on ``port``."""
    nets = parse_networks(networks) if isinstance(networks, str) else (networks or parse_networks(
        ",".join(default_networks())))
    ips = _hosts(nets)
    found = set(udp_sweep(ips, port))
    if tcp_fallback:
        rest = [ip for ip in ips if ip not in found]
        with ThreadPoolExecutor(max_workers=128) as pool:
            for ip, ok in zip(rest, pool.map(lambda a: tcp_open(a, port), rest)):
                if ok:
                    found.add(ip)
    return sorted(found, key=lambda a: tuple(int(x) for x in a.split(".")))


def connected_peers(local_ports=(90, 8081, 80)) -> list[str]:
    """IPv4 addresses currently talking to this computer on ``local_ports``.

    Terminals pushing (ADMS) to any server on this PC — e.g. the previous server on port 90 —
    show up here, so they are found with no address typed and nothing changed."""
    ports = {int(p) for p in local_ports}
    peers: set[str] = set()
    proc = "/proc/net/tcp"
    try:
        if os.path.exists(proc):
            with open(proc) as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    lip, lport = parts[1].split(":")
                    rip, _rport = parts[2].split(":")
                    if int(lport, 16) in ports and rip != "00000000":
                        peers.add(socket.inet_ntoa(bytes.fromhex(rip)[::-1]))
        else:
            out = subprocess.run(["netstat", "-an", "-p", "TCP"], capture_output=True, text=True,
                                 timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            for line in out.splitlines():
                cols = line.split()
                if len(cols) < 3 or not cols[0].upper().startswith("TCP"):
                    continue
                local, remote = cols[1], cols[2]
                lport = local.rsplit(":", 1)[-1]
                rip = remote.rsplit(":", 1)[0]
                if lport.isdigit() and int(lport) in ports and rip.count(".") == 3 and rip not in ("0.0.0.0",):
                    peers.add(rip)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return sorted(ip for ip in peers if not ip.startswith(("127.", "0.")))
