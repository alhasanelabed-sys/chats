"""Talk to a terminal on port 4370 (TCP, with automatic UDP fallback).

    with TerminalClient("10.0.0.20", comm_key=0) as term:
        info = term.device_info()
        users = term.get_users()
        fingers = term.get_templates()
        punches = term.get_attendance()
        term.set_time(datetime.now())

Pure standard library: replaces third-party packages (which often fail to
install on Windows and was the reason TCP reading never worked).
"""
from __future__ import annotations

import logging
import socket
import struct
from datetime import datetime

from . import protocol as P

log = logging.getLogger("hader.terminal")


class TerminalError(Exception):
    pass


class TerminalAuthError(TerminalError):
    pass


# Option keys read for the device card (``CMD_OPTIONS_RRQ``).
INFO_OPTIONS = ("~SerialNumber", "~DeviceName", "~Platform", "~OEMVendor", "MAC", "~ZKFPVersion",
                "ZKFaceVersion", "FaceFunOn", "~IsOnlyRFMachine", "PvFunOn", "PvVersion",
                "FingerFunOn", "~PIN2Width", "IPAddress", "NetMask", "GATEIPAddress",
                "ProductTime", "~ProductTime")

# Where a push (ADMS) terminal sends its data: the "Cloud Server Setting" screen.
SERVER_OPTIONS = ("WebServerIP", "WebServerPort", "ICLOCKSVRURL", "WebServerURLModel", "ServerIP",
                  "IsSupportPush", "PushServerIP", "PushServerPort")


class TerminalClient:
    MAX_CHUNK_TCP = 0xFFC0
    MAX_CHUNK_UDP = 16 * 1024

    def __init__(self, ip: str, port: int = 4370, comm_key: int | str = 0, timeout: float = 10,
                 udp: bool | None = None, encoding: str = "utf-8"):
        self.ip, self.port, self.timeout, self.encoding = ip, int(port), timeout, encoding
        try:
            self.comm_key = int(comm_key or 0)
        except ValueError as exc:
            raise TerminalError("communication key must be a number") from exc
        self.udp = udp  # None = try TCP then UDP
        self.sock: socket.socket | None = None
        self.session = 0
        self.reply = USHRT_START
        self.user_size = 72
        self._uid_pin: dict[int, str] | None = None
        self.sizes: P.Sizes | None = None
        self._rx = b""

    # ------------------------------------------------------------------ socket

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.disconnect()

    def connect(self) -> "TerminalClient":
        modes = [self.udp] if self.udp is not None else [False, True]
        last: Exception | None = None
        for udp in modes:
            try:
                self._open(udp)
                self._handshake()
                return self
            except TerminalAuthError:
                self._close()
                raise
            except (OSError, TerminalError) as exc:
                last = exc
                self._close()
        raise TerminalError(f"no answer from {self.ip}:{self.port} ({type(last).__name__}: {last})")

    def _open(self, udp: bool) -> None:
        self.udp = udp
        self.session, self.reply, self._rx = 0, USHRT_START, b""
        if udp:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.ip, self.port))
        else:
            self.sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)

    def _handshake(self) -> None:
        r = self.command(P.CMD_CONNECT)
        self.session = r.session
        if r.command == P.CMD_ACK_UNAUTH:
            r = self.command(P.CMD_AUTH, P.make_commkey(self.comm_key, self.session))
            if not r.ok:
                raise TerminalAuthError("wrong communication key (Comm Key) — check Menu > Comm. > Comm Key")
        elif not r.ok:
            raise TerminalError(f"connect refused (reply {r.command})")

    def _close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def disconnect(self) -> None:
        if self.sock is not None:
            try:
                self.command(P.CMD_EXIT)
            except (OSError, TerminalError):
                pass
        self._close()

    def _send(self, packet: bytes) -> None:
        assert self.sock is not None
        self.sock.sendall(packet if self.udp else P.tcp_wrap(packet))

    def _recv_exact(self, n: int) -> bytes:
        while len(self._rx) < n:
            chunk = self.sock.recv(max(65536, n - len(self._rx)))
            if not chunk:
                raise TerminalError("connection closed by the device")
            self._rx += chunk
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def recv_packet(self) -> P.Packet:
        try:
            if self.udp:
                return P.parse_packet(self.sock.recv(65536))
            head = self._recv_exact(8)
            if head[:4] != P.TCP_MAGIC:
                raise TerminalError("not a supported terminal (bad header)")
            size = struct.unpack("<I", head[4:8])[0]
            return P.parse_packet(self._recv_exact(size))
        except socket.timeout as exc:
            raise TerminalError("device did not answer in time") from exc

    def command(self, cmd: int, data: bytes = b"") -> P.Packet:
        self._send(P.build_packet(cmd, self.session, self.reply, data))
        self.reply = P.next_reply(self.reply)
        if cmd == P.CMD_EXIT and self.udp:
            return P.Packet(P.CMD_ACK_OK, 0, self.session, 0)
        return self.recv_packet()

    def _expect_ok(self, r: P.Packet, what: str) -> P.Packet:
        if not r.ok:
            raise TerminalError(f"{what}: device answered {r.command}")
        return r

    # ----------------------------------------------------------------- buffers

    def _receive_data(self, first: P.Packet) -> bytes:
        """Payload of a reply that is either CMD_DATA (inline) or CMD_PREPARE_DATA + chunks."""
        if first.command == P.CMD_DATA:
            return first.data
        if first.command != P.CMD_PREPARE_DATA:
            raise TerminalError(f"unexpected reply {first.command}")
        size = struct.unpack("<I", first.data[:4])[0]
        data = b""
        while True:
            p = self.recv_packet()
            if p.command == P.CMD_DATA:
                data += p.data
            elif p.command == P.CMD_ACK_OK:
                if len(data) >= size:
                    return data
            else:
                raise TerminalError(f"transfer interrupted (reply {p.command})")

    def read_buffer(self, cmd: int, fct: int = 0, ext: int = 0) -> bytes:
        """Ask the terminal to prepare a table and download it in chunks."""
        r = self.command(P.CMD_DATA_WRRQ, struct.pack("<bhii", 1, cmd, fct, ext))
        if r.command == P.CMD_DATA:
            return r.data
        self._expect_ok(r, "prepare table")
        size = struct.unpack("<I", r.data[1:5])[0] if len(r.data) >= 5 else 0
        chunk = self.MAX_CHUNK_UDP if self.udp else self.MAX_CHUNK_TCP
        data = b""
        try:
            for start in range(0, size, chunk):
                part = self.command(P.CMD_READ_BUFFER, struct.pack("<ii", start, min(chunk, size - start)))
                data += self._receive_data(part)
        finally:
            try:
                self.command(P.CMD_FREE_DATA)
            except (OSError, TerminalError):
                pass
        return data

    def send_buffer(self, buf: bytes) -> None:
        self.command(P.CMD_FREE_DATA)
        self._expect_ok(self.command(P.CMD_PREPARE_DATA, struct.pack("<I", len(buf))), "prepare upload")
        for start in range(0, len(buf), 1024):
            self._expect_ok(self.command(P.CMD_DATA, buf[start:start + 1024]), "upload")

    # ------------------------------------------------------------------- reads

    def get_option(self, key: str) -> str:
        r = self.command(P.CMD_OPTIONS_RRQ, key.encode() + b"\x00")
        if not r.ok:
            return ""
        return P.parse_option_reply(r.data)[1]

    def set_option(self, key: str, value: str) -> bool:
        return self.command(P.CMD_OPTIONS_WRQ, f"{key}={value}".encode() + b"\x00").ok

    def get_firmware(self) -> str:
        r = self.command(P.CMD_GET_VERSION)
        return P.cstr(r.data, "latin-1") if r.ok else ""

    def read_sizes(self) -> P.Sizes:
        r = self._expect_ok(self.command(P.CMD_GET_FREE_SIZES), "read sizes")
        self.sizes = P.parse_sizes(r.data)
        return self.sizes

    PALM_OPTIONS = ("~PvCount", "PvCount", "~PalmCount", "PalmCount")

    def palm_count(self) -> int | None:
        """Palm templates on palm terminals (not every firmware reports it over 4370)."""
        for key in self.PALM_OPTIONS:
            try:
                v = self.get_option(key)
            except TerminalError:
                continue
            if v.strip().isdigit():
                return int(v)
        return None

    def counters(self) -> dict[str, str]:
        """Quick status: user / fingerprint / face / palm / record counts and the clock."""
        s = self.read_sizes()
        out = {"UserCount": str(s.users), "FPCount": str(s.fingers), "FaceCount": str(s.faces),
               "TransactionCount": str(s.records)}
        palm = self.palm_count()
        if palm is not None:
            out["PvCount"] = str(palm)
        try:
            out["DeviceTime"] = self.get_time().isoformat(sep=" ")
        except TerminalError:
            pass
        return out

    def get_time(self) -> datetime:
        return P.decode_time(self._expect_ok(self.command(P.CMD_GET_TIME), "read time").data)

    def set_time(self, when: datetime) -> None:
        self._expect_ok(self.command(P.CMD_SET_TIME, struct.pack("<I", P.encode_time(when))), "set time")

    def device_info(self) -> dict[str, str]:
        """Same keys as an ADMS INFO/options upload, so one code path stores both."""
        info: dict[str, str] = {}
        for key in INFO_OPTIONS:
            try:
                v = self.get_option(key)
            except TerminalError:
                continue
            if v:
                info[key.lstrip("~")] = v
        info["FWVersion"] = self.get_firmware()
        s = self.read_sizes()
        info.update(UserCount=str(s.users), FPCount=str(s.fingers), TransactionCount=str(s.records),
                    FaceCount=str(s.faces), MaxUserCount=str(s.users_cap), MaxFingerCount=str(s.fingers_cap),
                    MaxAttLogCount=str(s.records_cap), MaxFaceCount=str(s.faces_cap))
        palm = self.palm_count()
        if palm is not None:
            info["PvCount"] = str(palm)
        if info.get("ZKFPVersion"):
            info["FPVersion"] = info["ZKFPVersion"]
        if info.get("ZKFaceVersion"):
            info["FaceVersion"] = info["ZKFaceVersion"]
        try:
            info["DeviceTime"] = self.get_time().isoformat(sep=" ")
        except TerminalError:
            pass
        return {k: v for k, v in info.items() if v not in ("", None)}

    def server_settings(self) -> dict[str, str]:
        """Where the terminal currently pushes (Cloud Server Setting)."""
        out = {}
        for key in SERVER_OPTIONS:
            try:
                v = self.get_option(key)
            except TerminalError:
                continue
            if v:
                out[key] = v
        return out

    def get_users(self) -> list[P.DevUser]:
        self.read_sizes()  # fresh count: someone may have enrolled since the last read
        data = self.read_buffer(P.CMD_USERTEMP_RRQ, P.FCT_USER)
        users, self.user_size = P.parse_users(data, self.sizes.users, self.encoding)
        self._uid_pin = {u.uid: u.user_id for u in users}
        return users

    def get_templates(self) -> list[P.DevTemplate]:
        """All fingerprint templates (fingerprint algorithm 10, base64 of these bytes = ADMS ``TMP``)."""
        data = self.read_buffer(P.CMD_DB_RRQ, P.FCT_FINGERTMP)
        out = P.parse_templates(data)
        pins = self._uid_pins()
        for t in out:
            t.user_id = pins.get(t.uid, str(t.uid))
        return out

    def get_face(self, uid: int) -> bytes | None:
        """Near-infrared face template of one user (face algorithm 7, fid 50)."""
        r = self.command(P.CMD_GET_USERTEMP, struct.pack("<hb", uid, P.FACE_FID))
        if r.command not in (P.CMD_DATA, P.CMD_PREPARE_DATA):
            return None
        return self._receive_data(r) or None

    def get_faces(self, users: list[P.DevUser] | None = None) -> list[P.DevTemplate]:
        users = users if users is not None else self.get_users()
        out = []
        for u in users:
            try:
                tpl = self.get_face(u.uid)
            except TerminalError:
                continue
            if tpl:
                out.append(P.DevTemplate(uid=u.uid, fid=P.FACE_FID, valid=1, template=tpl, user_id=u.user_id))
        return out

    def get_attendance(self) -> list[P.DevPunch]:
        pins = self._uid_pins()
        self.read_sizes()  # fresh count: employees keep punching while we read
        data = self.read_buffer(P.CMD_ATTLOG_RRQ)
        self.last_attlog_raw = data
        self.last_attlog_stats = {}
        return P.parse_attlog(data, self.sizes.records, pins, diagnostics=self.last_attlog_stats)

    def _uid_pins(self) -> dict[int, str]:
        if self._uid_pin is None:
            self.get_users()
        return self._uid_pin or {}

    # ------------------------------------------------------------------ writes

    def enable(self, on: bool = True) -> None:
        self._expect_ok(self.command(P.CMD_ENABLEDEVICE if on else P.CMD_DISABLEDEVICE),
                        "enable device" if on else "disable device")

    def refresh(self) -> None:
        self.command(P.CMD_REFRESHDATA)

    def restart(self) -> None:
        self._send(P.build_packet(P.CMD_RESTART, self.session, self.reply))
        self._close()

    def clear_attendance(self) -> None:
        self._expect_ok(self.command(P.CMD_CLEAR_ATTLOG), "clear records")

    def test_voice(self, index: int = 0) -> None:
        self.command(P.CMD_TESTVOICE, struct.pack("<I", index))

    def _free_uid(self, users: list[P.DevUser]) -> int:
        used = {u.uid for u in users}
        uid = 1
        while uid in used:
            uid += 1
        return uid

    def save_user(self, user_id: str, name: str = "", privilege: int = 0, password: str = "",
                  card: int = 0, group_id: str = "1", users: list[P.DevUser] | None = None) -> P.DevUser:
        """Add or update a user (matched by PIN)."""
        users = users if users is not None else self.get_users()
        current = next((u for u in users if u.user_id == str(user_id)), None)
        u = P.DevUser(uid=current.uid if current else self._free_uid(users), user_id=str(user_id), name=name,
                      privilege=privilege, password=password, card=int(card or 0), group_id=group_id)
        self._expect_ok(self.command(P.CMD_USER_WRQ, P.pack_user(u, self.user_size, self.encoding)),
                        f"save user {user_id}")
        if current is None:
            users.append(u)
        else:
            users[users.index(current)] = u
        if self._uid_pin is not None:
            self._uid_pin[u.uid] = u.user_id
        return u

    def save_user_templates(self, user: P.DevUser, templates: list[P.DevTemplate]) -> None:
        """Write a user and their fingerprints in one go (CMD_SAVE_USERTEMPS)."""
        for t in templates:
            t.uid = user.uid
        self.send_buffer(P.build_usertemps([user], {user.uid: templates}, self.user_size))
        self._expect_ok(self.command(P.CMD_SAVE_USERTEMPS, struct.pack("<IHH", 12, 0, 8)), "save templates")
        self.refresh()

    def delete_user(self, user_id: str, users: list[P.DevUser] | None = None) -> bool:
        users = users if users is not None else self.get_users()
        u = next((x for x in users if x.user_id == str(user_id)), None)
        if u is None:
            return False
        self._expect_ok(self.command(P.CMD_DELETE_USER, struct.pack("<h", u.uid)), f"delete user {user_id}")
        users.remove(u)
        return True

    def delete_templates(self, user: P.DevUser, fid: int | None = None) -> None:
        """Delete one finger (0-9) or, with ``fid=None``, all fingers of a user."""
        for f in ([fid] if fid is not None else range(10)):
            self.command(P.CMD_DELETE_USERTEMP, struct.pack("<hb", user.uid, f))
        self.refresh()

    def clear_all_data(self) -> None:
        self._expect_ok(self.command(P.CMD_CLEAR_DATA), "clear data")

    def point_to_server(self, server_ip: str, server_port: int) -> dict[str, str]:
        """Write the Cloud Server (ADMS) address into the terminal, so it pushes here.

        Returns the server settings read back afterwards. Firmware differs in which
        keys it uses, so all common ones are written; unknown keys are ignored."""
        url = f"http://{server_ip}:{server_port}"
        for key, value in (("WebServerIP", server_ip), ("WebServerPort", str(server_port)),
                           ("ICLOCKSVRURL", url), ("WebServerURLModel", "0"), ("ServerIP", server_ip),
                           ("IsSupportPush", "1")):
            try:
                self.set_option(key, value)
            except TerminalError:
                pass
        self.command(P.CMD_REFRESHOPTION)
        return self.server_settings()


USHRT_START = P.USHRT_MAX - 1
