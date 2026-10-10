"""Direct link to terminals on port 4370 — our own implementation, no SDK.

Two ways a terminal works with this program:

* **ADMS / Push** (``adms/``): the terminal connects to us. Everything travels,
  including visible-light faces, palms and photos.
* **Direct 4370 link** (this module): we connect to the terminal. Used for
  terminals *found on the network by themselves* (``discover``) or whose IP is
  entered. It reads device info, users, fingerprints, near-infrared faces and
  punches, and executes the same command queue the ADMS side uses (users,
  fingerprints, time, reboot, clear...), so the area model works for both.

A terminal that starts pushing over ADMS automatically leaves direct mode.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from . import models as m
from . import store
from .adms.protocol import AttRecord, OperItem, parse_kv
from .db import now, session_scope
from .terminal import discovery
from .terminal import protocol as ZP
from .terminal.client import TerminalAuthError, TerminalClient, TerminalError

log = logging.getLogger("hader.tcp")

# sn -> {"time", "ok", "read", "new", "users", "templates", "error"} for the UI
STATUS: dict[str, dict] = {}
# Last network scan, for the UI
DISCOVERY: dict = {"running": False, "time": "", "networks": "", "found": [], "error": ""}
_device_locks: dict[str, threading.Lock] = {}
_scan_lock = threading.Lock()


class TcpPullError(Exception):
    pass


def writing_enabled(db) -> bool:
    """Writing is an explicit administrator decision, even when port 90 is free.

    Ownership of a socket proves that this process can reach a terminal; it does
    not prove that the operator intended changes to be sent to production devices.
    """
    return bool(store.get(db, "tcp.write_back"))


def _lock(key: str) -> threading.Lock:
    return _device_locks.setdefault(key, threading.Lock())


@dataclass
class DeviceRead:
    punches: list[AttRecord] = field(default_factory=list)
    users: list[dict] = field(default_factory=list)          # shaped like an ADMS USER line
    templates: list[OperItem] = field(default_factory=list)  # FP / FACE items, like an ADMS upload
    info: dict = field(default_factory=dict)
    bio_read: bool = False  # templates were read this time (so missing ones can be detected)


def _client(ip: str, port: int, comm_key, timeout: float) -> TerminalClient:
    try:
        return TerminalClient(ip, int(port or 4370), comm_key or 0, timeout=timeout).connect()
    except TerminalError as exc:
        raise TcpPullError(str(exc)) from exc


def users_as_rows(users: list[ZP.DevUser]) -> list[dict]:
    return [{"pin": u.user_id, "name": u.name, "card": str(u.card or ""), "passwd": u.password,
             "pri": str(u.privilege)} for u in users]


def templates_as_items(templates: list[ZP.DevTemplate]) -> list[OperItem]:
    out = []
    for t in templates:
        b64 = base64.b64encode(t.template).decode()
        if t.fid == ZP.FACE_FID:
            out.append(OperItem("FACE", data={"pin": t.user_id, "fid": "0", "valid": "1", "tmp": b64}))
        else:
            out.append(OperItem("FP", data={"pin": t.user_id, "fid": str(t.fid), "valid": str(t.valid or 1),
                                            "tmp": b64}))
    return out


def read_device(ip: str, port: int = 4370, comm_key: str = "0", timeout: int = 15,
                with_users: bool = True, with_bio: bool = True) -> DeviceRead:
    """Read everything a terminal holds (the terminal keeps working meanwhile)."""
    term = _client(ip, port, comm_key, timeout)
    try:
        out = DeviceRead(info=term.device_info())
        users = term.get_users()
        if with_users:
            out.users = users_as_rows(users)
        if with_bio:
            tpls = term.get_templates() if term.sizes and term.sizes.fingers else []
            if term.sizes and term.sizes.faces:
                tpls += term.get_faces(users)
            out.templates = templates_as_items(tpls)
            out.bio_read = True
        out.punches = [AttRecord(pin=p.user_id, time=p.time, state=p.state, verify=p.verify,
                                 work_code=p.work_code) for p in term.get_attendance()]
        stats = dict(getattr(term, "last_attlog_stats", None) or ZP.LAST_ATTLOG)
        out.info["AttLogLayout"] = f"{stats.get('size')}B {stats.get('valid')}/{stats.get('records')}"
        if stats.get("records") and stats["valid"] < stats["records"]:
            _dump_raw(ip, "attlog", getattr(term, "last_attlog_raw", b""), stats)
        return out
    except TerminalError as exc:
        raise TcpPullError(str(exc)) from exc
    finally:
        term.disconnect()


def _dump_raw(ip: str, table: str, raw: bytes, stats: dict) -> None:
    """Keep the raw table when some records could not be decoded, for diagnosis."""
    from .config import settings
    log.warning("%s: %s records of %s from %s could not be decoded (layout %s bytes)",
                table, stats.get("records", 0) - stats.get("valid", 0), stats.get("records"), ip, stats.get("size"))
    try:
        folder = settings.data_dir / "diag"
        folder.mkdir(exist_ok=True)
        if hasattr(folder, "chmod") and __import__("os").name != "nt":
            folder.chmod(0o700)
        digest = hashlib.sha256(raw).hexdigest()[:16]
        path = folder / f"{ip.replace(':', '_')}-{table}-{digest}.bin"
        path.write_bytes(raw)
        if __import__("os").name != "nt":
            path.chmod(0o600)
    except OSError:
        pass


def store_read(sn: str, data: DeviceRead) -> dict:
    """Save the complete terminal log using the database uniqueness key.

    Comparing every record makes a periodic repair recover punches that arrive out
    of order or after a device clock correction; SQLite rejects duplicates cheaply.
    """
    from .adms import sync
    with session_scope() as db:
        dev = db.scalar(select(m.Device).where(m.Device.sn == sn))
        if dev is None:
            raise TcpPullError(f"device {sn} not found")
        if data.info:
            sync.apply_device_info(dev, data.info)  # algorithm versions first: templates are tagged with them
        records = data.punches
        full = list(store.get(db, "tcp.full_compare") or [])
        last = db.scalar(select(func.max(m.Transaction.punch_time)).where(m.Transaction.device_sn == sn))
        if sn in full:
            full.remove(sn)  # compare the whole log once (after a repair)
            store.set_(db, "tcp.full_compare", full)
        new = sync.save_punches(db, dev, records, source="tcp")
        for u in data.users:
            emp, changed = sync._apply_user(db, dev, u)
            if emp is not None and changed:
                db.flush()
                sync._distribute_user(db, emp, sn)
        if data.templates:
            sync.apply_oper_items(db, dev, data.templates)
        queued = reconcile(db, dev, data) if writing_enabled(db) else 0
        dev.last_sync = dev.last_activity = now()
    return {"read": len(records), "new": len(new), "users": len(data.users), "templates": len(data.templates),
            "queued": queued}


def reconcile(db, dev: m.Device, data: DeviceRead) -> int:
    """Area sync,: every active employee of the terminal's area must be on it
    with their fingerprints. Compares what was just read and queues only what is missing
    (people enrolled on another terminal of the area, or added here)."""
    from .adms import sync
    if not data.users:
        return 0
    on_device = {u["pin"] for u in data.users}
    fingers = {(t.data.get("pin"), str(t.data.get("fid"))) for t in data.templates if t.kind == "FP"}
    n = 0
    for emp in sync.device_employees(db, dev):
        if emp.emp_code not in on_device:
            n += sync.push_employee_to_device(db, dev, emp, with_bio=True)
            continue
        if not data.bio_read:
            continue  # templates were not read this time: nothing to compare
        missing = [t for t in db.scalars(select(m.BioTemplate).where(
            m.BioTemplate.employee_id == emp.id, m.BioTemplate.bio_type == 1)).all()
            if (emp.emp_code, str(t.bio_no)) not in fingers]
        for content, title in sync.template_commands(db, dev, emp, missing) if missing else []:
            if sync.queue(db, dev.sn, content, title):
                n += 1
    return n


def read_and_store(sn: str, with_bio: bool = True) -> dict:
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        if d is None or not d.ip:
            raise TcpPullError("device IP address is not set")
        ip, port, key = d.ip, d.tcp_port or 4370, d.comm_key or "0"
    with _lock(sn):
        try:
            result = store_read(sn, read_device(ip, port, key, with_bio=with_bio))
        except TcpPullError as exc:
            STATUS[sn] = {"time": now().isoformat(sep=" "), "ok": False, "error": str(exc)}
            raise
    STATUS[sn] = {"time": now().isoformat(sep=" "), "ok": True, **result}
    return result


# --------------------------------------------------------------------------
# Executing the command queue over 4370
# --------------------------------------------------------------------------

def _int(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


class _Unsupported(Exception):
    pass


def _run_one(term: TerminalClient, users: list[ZP.DevUser], content: str) -> str:
    """Execute one queued ADMS command on a terminal connected over 4370."""
    def find(pin):
        return next((u for u in users if u.user_id == pin), None)

    if content.startswith(("DATA UPDATE ", "DATA DELETE ")):
        _data, verb, table, *rest = content.split(" ", 3)
        kv = parse_kv(rest[0] if rest else "")
        pin = kv.get("pin", "")
        if verb == "UPDATE" and table == "USERINFO":
            card = kv.get("card", "").strip("[]")
            term.save_user(pin, kv.get("name", ""), _int(kv.get("pri")), kv.get("passwd", ""),
                         _int(card) if card.isdigit() else 0, users=users)
            return "user saved"
        if verb == "DELETE" and table == "USERINFO":
            return "user deleted" if term.delete_user(pin, users) else "user was not on the device"
        if verb == "UPDATE" and (table == "FINGERTMP" or (table == "BIODATA" and _int(kv.get("type")) == 1)):
            u = find(pin)
            if u is None:
                raise TerminalError(f"user {pin} is not on the device")
            fid = _int(kv.get("fid", kv.get("no")))
            tpl = base64.b64decode(kv.get("tmp", ""))
            term.save_user_templates(u, [ZP.DevTemplate(uid=u.uid, fid=fid, valid=_int(kv.get("valid"), 1) or 1,
                                                      template=tpl)])
            return f"fingerprint {fid} saved"
        if verb == "DELETE" and table in ("FINGERTMP", "BIODATA"):
            u = find(pin)
            if u is None:
                return "user was not on the device"
            if table == "BIODATA" and kv.get("type") not in (None, "", "1"):
                raise _Unsupported("only fingerprints can be deleted over 4370")
            term.delete_templates(u, _int(kv["fid"]) if "fid" in kv else None)
            return "fingerprints deleted"
        raise _Unsupported(f"{table} travels only over ADMS (point the terminal to this server)")
    if content.startswith("SET OPTION DateTime="):
        term.set_time(now())
        return "time set"
    if content in ("INFO", "CHECK", "RELOAD OPTIONS"):
        return "ok"
    if content == "REBOOT":
        term.restart()
        return "restarting"
    if content == "CLEAR LOG":
        term.clear_attendance()
        return "records cleared"
    if content == "CLEAR DATA":
        term.clear_all_data()
        users.clear()
        return "data cleared"
    if content == "CLEAR PHOTO":
        return "no photos over 4370"
    if content.startswith("DATA QUERY"):
        return "read with the next direct read"
    raise _Unsupported("not available over 4370 (needs ADMS)")


def deliver_pending(sn: str, limit: int = 500) -> dict:
    """Run the queued commands of a direct-mode terminal. Returns counters."""
    with session_scope() as db:
        if not writing_enabled(db):
            return {"done": 0, "failed": 0, "read_only": True}
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        if d is None or not d.ip:
            return {"done": 0, "failed": 0}
        ip, port, key = d.ip, d.tcp_port or 4370, d.comm_key or "0"
        cmds = [(c.id, c.content) for c in db.scalars(select(m.DeviceCommand).where(
            m.DeviceCommand.device_sn == sn, m.DeviceCommand.status == "pending")
            .order_by(m.DeviceCommand.id).limit(limit)).all()]
    if not cmds:
        return {"done": 0, "failed": 0}
    results: list[tuple[int, str, str, str]] = []
    with _lock(sn):
        term = _client(ip, port, key, 20)
        rebooted = False
        disabled = False
        try:
            term.enable(False)  # keypad locked while writing
            disabled = True
            users = term.get_users()
            for cid, content in cmds:
                try:
                    msg = _run_one(term, users, content)
                    results.append((cid, "done", "0", msg))
                except _Unsupported as exc:
                    results.append((cid, "failed", "-1002", str(exc)))
                except (TerminalError, ValueError) as exc:
                    results.append((cid, "failed", "-1", str(exc)))
                if content == "REBOOT":
                    rebooted = True
                    break
            if not rebooted:
                term.refresh()
        except TerminalError as exc:
            log.warning("direct delivery to %s stopped: %s", sn, exc)
        finally:
            if disabled and not rebooted:
                try:
                    term.enable(True)
                except (TerminalError, OSError):
                    log.warning("could not re-enable terminal %s after a failed write", sn)
            if not rebooted:
                term.disconnect()
    with session_scope() as db:
        ts = now()
        for cid, status, code, msg in results:
            c = db.get(m.DeviceCommand, cid)
            if c is not None:
                c.status, c.return_code, c.result, c.sent_at, c.returned_at = status, code, msg, ts, ts
                c.attempts += 1
    from .events import publish
    publish("commands", sn=sn)
    return {"done": sum(r[1] == "done" for r in results), "failed": sum(r[1] == "failed" for r in results)}


def device_server_settings(sn: str) -> dict:
    """Where the terminal pushes now (its Cloud Server Setting)."""
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        ip, port, key = d.ip, d.tcp_port or 4370, d.comm_key or "0"
    with _lock(sn):
        term = _client(ip, port, key, 10)
        try:
            return term.server_settings()
        except TerminalError as exc:
            raise TcpPullError(str(exc)) from exc
        finally:
            term.disconnect()


def local_ip_for(device_ip: str) -> str:
    """This computer's address on the terminal's network."""
    ips = discovery.local_ipv4()
    try:
        target = ipaddress.ip_address(device_ip)
        for ip in ips:
            if target in ipaddress.ip_network(f"{ip}/24", strict=False):
                return ip
    except ValueError:
        pass
    return ips[0] if ips else ""


def point_to_this_server(sn: str, server_ip: str = "", server_port: int = 0, reboot: bool = True) -> dict:
    """Switch a terminal to push (ADMS) to this program, without touching its keypad."""
    from .config import settings
    with session_scope() as db:
        if not writing_enabled(db):
            raise TcpPullError("read-only mode: enable tcp.write_back before changing terminal settings")
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        ip, port, key = d.ip, d.tcp_port or 4370, d.comm_key or "0"
    server_ip = server_ip or local_ip_for(ip)
    server_port = server_port or (settings.adms_ports[0] if settings.adms_ports else settings.web_port)
    with _lock(sn):
        term = _client(ip, port, key, 10)
        try:
            after = term.point_to_server(server_ip, server_port)
            if reboot:
                term.restart()
        except TerminalError as exc:
            raise TcpPullError(str(exc)) from exc
        finally:
            term.disconnect()
    return {"server": f"{server_ip}:{server_port}", "device_settings": after, "rebooted": reboot}


# --------------------------------------------------------------------------
# Discovery: terminals register themselves
# --------------------------------------------------------------------------

def identify(ip: str, port: int = 4370, keys: list[str] | None = None, timeout: float = 6) -> tuple[dict, str]:
    """Connect with each candidate comm key; -> (device info, working key)."""
    last: Exception | None = None
    for key in keys or ["0"]:
        try:
            with TerminalClient(ip, port, key, timeout=timeout) as term:
                return term.device_info(), key
        except TerminalAuthError as exc:
            last = exc
        except TerminalError as exc:
            raise TcpPullError(str(exc)) from exc
    raise TcpPullError(str(last) if last else "no answer")


def register_found(ip: str, info: dict, key: str, port: int = 4370) -> tuple[str, bool]:
    """Create (or update the address of) a terminal found on the network."""
    from .adms import sync
    sn = (info.get("SerialNumber") or "").strip()
    if not sn:
        raise TcpPullError("terminal did not report a serial number")
    with session_scope() as db:
        dev = db.scalar(select(m.Device).where(m.Device.sn == sn))
        created = False
        if dev is None:
            if not store.get(db, "adms.auto_add"):
                return sn, False
            area_id = store.get(db, "adms.default_area") or None
            if area_id and not db.get(m.Area, area_id):
                area_id = None
            dev = m.Device(sn=sn, alias=f"{info.get('DeviceName') or 'Terminal'} {ip}", area_id=area_id, ip=ip,
                           comm_key=str(key), tcp_port=port, tcp_poll=True, managed_by="tcp")
            db.add(dev)
            db.flush()
            db.add(m.AuditLog(username="discovery", action="device.register", target=sn, detail=ip))
            log.info("terminal found on the network: %s at %s", sn, ip)
            created = True
        else:
            dev.ip, dev.comm_key, dev.tcp_port = ip, str(key), port
        sync.apply_device_info(dev, info)
        if created:
            if writing_enabled(db):
                sync.sync_device(db, dev)  # this area's employees go to the new terminal
        dev.last_activity = now()
    return sn, created


def discover(networks: str | None = None, port: int = 4370, peers_only: bool = False) -> dict:
    """Find, identify and register terminals. Only one search runs at a time.

    Terminals already talking to this PC (ADMS to port 90/8081, e.g. to the previous server)
    are found from the PC's open connections — no address to type. ``peers_only``
    skips the network sweep and only checks such terminals not registered yet."""
    if not _scan_lock.acquire(blocking=False):
        return dict(DISCOVERY)
    try:
        with session_scope() as db:
            nets = networks if networks is not None else (store.get(db, "discovery.networks") or "")
            keys = [k.strip() for k in str(store.get(db, "discovery.comm_keys") or "0").split(",") if k.strip()]
            known = {d.ip: d.comm_key for d in db.scalars(select(m.Device)).all() if d.ip}
        if not nets:  # this PC's network plus the networks of terminals already known
            extra = [f"{ip.rsplit('.', 1)[0]}.0/24" for ip in known if ip.count(".") == 3]
            nets = ",".join(dict.fromkeys(discovery.default_networks() + extra))
        DISCOVERY.update(running=True, networks=nets, error="")
        from .config import settings
        peers = discovery.connected_peers({90, 80, settings.web_port, *settings.adms_ports})
        if peers_only:
            ips = [ip for ip in peers if ip not in known]
            if not ips:
                return dict(DISCOVERY)
        else:
            ips = list(dict.fromkeys(peers + discovery.scan(nets, port)))
        found = []
        for ip in ips:
            entry: dict = {"ip": ip}
            try:
                tried = ([known[ip]] if known.get(ip) else []) + keys + ["0"]
                info, key = identify(ip, port, list(dict.fromkeys(tried)))
                sn, created = register_found(ip, info, key, port)
                entry.update(sn=sn, model=info.get("DeviceName", ""), new=created, ok=True)
                if created:
                    try:
                        entry["read"] = read_and_store(sn)
                    except TcpPullError as exc:
                        entry["read_error"] = str(exc)
            except (TcpPullError, TerminalError, OSError) as exc:
                entry.update(ok=False, error=str(exc))
            found.append(entry)
        DISCOVERY.update(found=found, time=now().isoformat(sep=" "), running=False)
        return dict(DISCOVERY)
    except ValueError as exc:
        DISCOVERY["error"] = str(exc)
        raise TcpPullError(str(exc)) from exc
    finally:
        DISCOVERY["running"] = False
        _scan_lock.release()


# --------------------------------------------------------------------------
# Scheduler (called every minute by the maintenance loop)
# --------------------------------------------------------------------------

_COUNTS = ("user_count", "fp_count", "face_count", "palm_count", "att_count")


def quick_status(sn: str) -> dict:
    """Connect briefly and refresh state + counters (seconds, even for 100,000 records).

    Returns {"online", "changed": {"users", "bio", "punches"}} so the caller reads only
    what actually changed on the terminal."""
    from .adms import sync
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        ip, port, key = d.ip, d.tcp_port or 4370, d.comm_key or "0"
        before = {k: getattr(d, k) for k in _COUNTS}
        never_read = d.last_sync is None
    lock = _lock(sn)
    if not lock.acquire(timeout=1):
        return {"online": True, "changed": {}, "busy": True}  # a full read is running right now
    try:
        try:
            term = TerminalClient(ip, port, key, timeout=6).connect()
        except TerminalError as exc:
            STATUS[sn] = {**STATUS.get(sn, {}), "time": now().isoformat(sep=" "), "ok": False, "error": str(exc)}
            return {"online": False, "changed": {}}
        try:
            counters = term.counters()
        except TerminalError as exc:
            STATUS[sn] = {**STATUS.get(sn, {}), "time": now().isoformat(sep=" "), "ok": False, "error": str(exc)}
            return {"online": False, "changed": {}}
        finally:
            term.disconnect()
    finally:
        lock.release()
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        sync.apply_device_info(d, counters)
        d.last_activity = now()
        after = {k: getattr(d, k) for k in _COUNTS}
    changed = {
        "users": never_read or after["user_count"] != before["user_count"],
        "bio": never_read or any(after[k] != before[k] for k in ("fp_count", "face_count", "palm_count")),
        "punches": never_read or after["att_count"] != before["att_count"],
    }
    return {"online": True, "changed": changed}


def _poll_one(sn: str, direct: bool, write_back: bool, with_bio: bool, full_due: bool) -> None:
    try:
        if direct and write_back:
            deliver_pending(sn)
        st = quick_status(sn)
        ch = st.get("changed", {})
        if not st["online"] or st.get("busy"):
            return
        if full_due or ch.get("users") or ch.get("bio") or ch.get("punches"):
            read_and_store(sn, with_bio=with_bio and (full_due or bool(ch.get("users") or ch.get("bio"))))
    except TcpPullError as exc:
        log.warning("direct read of %s failed: %s", sn, exc)
    except Exception:  # noqa: BLE001 - one terminal must never stop the others
        log.exception("direct read of %s failed", sn)


def poll_due(state: dict) -> None:
    """Every minute: a quick status check of every direct terminal (state + counters),
    then a read of exactly what changed (new punches, users, templates). A full read
    still runs every ``tcp.poll_minutes`` as a safety net. Terminals run in parallel,
    so one switched-off terminal never delays the others."""
    from concurrent.futures import ThreadPoolExecutor
    with session_scope() as db:
        every = max(1, int(store.get(db, "tcp.poll_minutes") or 5))
        with_bio = bool(store.get(db, "tcp.read_bio"))
        write_back = writing_enabled(db)
        devices = [(d.sn, d.managed_by == "tcp") for d in db.scalars(select(m.Device).where(
            m.Device.enabled.is_(True))).all() if d.ip and (d.tcp_poll or d.managed_by == "tcp")]
    stamp = now().timestamp()
    jobs = []
    for sn, direct in devices:
        full_due = stamp - state.get(("tcp", sn), 0) >= every * 60
        if full_due:
            state[("tcp", sn)] = stamp
        jobs.append((sn, direct, write_back, with_bio, full_due))
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
        list(pool.map(lambda j: _poll_one(*j), jobs))


def discover_due(state: dict) -> None:
    with session_scope() as db:
        on = bool(store.get(db, "discovery.enabled"))
        every = max(5, int(store.get(db, "discovery.minutes") or 30))
    state.setdefault("discover_at", now().timestamp() - every * 60 + 60)  # first scan 1 min after start
    if not on:
        return
    if now().timestamp() - state["discover_at"] < every * 60:
        try:  # every minute: terminals that just started talking to this PC (e.g. to the previous server on port 90)
            discover(peers_only=True)
        except Exception:  # noqa: BLE001
            log.exception("connection-based discovery failed")
        return
    state["discover_at"] = now().timestamp()
    try:
        discover()
    except Exception:  # noqa: BLE001 - never stop the maintenance loop
        log.exception("network discovery failed")
