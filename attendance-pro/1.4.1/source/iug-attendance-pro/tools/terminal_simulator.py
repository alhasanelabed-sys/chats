"""A simulated terminal speaking the 4370 protocol (TCP and UDP).

Used by the tests, and handy to try the program without a real device:

    python tools/terminal_simulator.py --port 4370 --key 0
"""
from __future__ import annotations

import argparse
import socketserver
import struct
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hader.terminal import protocol as P  # noqa: E402


class FakeTerminal:
    def __init__(self, sn: str = "SIMT4370001", comm_key: int = 0, model: str = "SpeedFace-V5L",
                 user_size: int = 72):
        self.sn, self.comm_key, self.model, self.user_size = sn, comm_key, model, user_size
        self.users: list[P.DevUser] = []
        self.templates: list[P.DevTemplate] = []
        self.faces: dict[int, bytes] = {}
        self.punches: list[P.DevPunch] = []
        self.options: dict[str, str] = {
            "~SerialNumber": sn, "~DeviceName": model, "~Platform": "ZMM220_TFT", "MAC": "00:17:61:12:34:56",
            "~ZKFPVersion": "10", "ZKFaceVersion": "7", "FaceFunOn": "1", "WebServerIP": "10.0.0.5",
            "WebServerPort": "90"}
        self.clock = datetime(2026, 10, 5, 8, 0, 0)
        self.lock = threading.Lock()
        self.commands: list[int] = []

    # sample data ---------------------------------------------------------
    def seed(self, n_users: int = 3, n_punches: int = 5) -> "FakeTerminal":
        for i in range(1, n_users + 1):
            self.users.append(P.DevUser(uid=i, user_id=str(100 + i), name=f"User {i}", card=5000 + i))
            self.templates.append(P.DevTemplate(uid=i, fid=0, valid=1, template=bytes([i]) * 600))
            self.faces[i] = bytes([0x40 + i]) * 2000
        t0 = datetime(2026, 10, 4, 7, 55)
        for k in range(n_punches):
            u = self.users[k % len(self.users)]
            self.punches.append(P.DevPunch(u.user_id, t0 + timedelta(minutes=k), k % 2, 1, uid=u.uid))
        return self

    # protocol ----------------------------------------------------------------
    def handle(self, state: dict, raw: bytes) -> list[bytes]:
        """One incoming packet -> outgoing packets."""
        pkt = P.parse_packet(raw)
        out: list[bytes] = []

        def reply(cmd, data=b""):
            out.append(P.build_packet(cmd, state["session"], pkt.reply, data))

        with self.lock:
            self.commands.append(pkt.command)
            c = pkt.command
            if c == P.CMD_CONNECT:
                state["session"] = state.get("next_session", 0x1234)
                state["auth"] = self.comm_key == 0
                reply(P.CMD_ACK_OK if state["auth"] else P.CMD_ACK_UNAUTH)
                return out
            if c == P.CMD_AUTH:
                state["auth"] = pkt.data == P.make_commkey(self.comm_key, state["session"])
                reply(P.CMD_ACK_OK if state["auth"] else P.CMD_ACK_UNAUTH)
                return out
            if not state.get("auth"):
                reply(P.CMD_ACK_UNAUTH)
                return out
            if c == P.CMD_EXIT:
                reply(P.CMD_ACK_OK)
                state["closed"] = True
            elif c == P.CMD_OPTIONS_RRQ:
                key = P.cstr(pkt.data, "latin-1")
                if key in self.options:
                    reply(P.CMD_ACK_OK, f"{key}={self.options[key]}".encode() + b"\x00")
                else:
                    reply(P.CMD_ACK_ERROR)
            elif c == P.CMD_OPTIONS_WRQ:
                k, _, v = P.cstr(pkt.data, "latin-1").partition("=")
                self.options[k] = v
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_GET_VERSION:
                reply(P.CMD_ACK_OK, b"Ver 6.60 Sep 2 2024\x00")
            elif c == P.CMD_GET_FREE_SIZES:
                reply(P.CMD_ACK_OK, P.build_sizes(P.Sizes(
                    users=len(self.users), fingers=len(self.templates), records=len(self.punches),
                    faces=len(self.faces), users_cap=10000, fingers_cap=6000, records_cap=200000,
                    faces_cap=3000)))
            elif c == P.CMD_GET_TIME:
                reply(P.CMD_ACK_OK, struct.pack("<I", P.encode_time(self.clock)))
            elif c == P.CMD_SET_TIME:
                self.clock = P.decode_time(pkt.data)
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_DATA_WRRQ:
                _one, cmd, fct, _ext = struct.unpack("<bhii", pkt.data[:11])
                if cmd == P.CMD_USERTEMP_RRQ and fct == P.FCT_USER:
                    buf = P.build_users(self.users, self.user_size)
                elif cmd == P.CMD_DB_RRQ and fct == P.FCT_FINGERTMP:
                    buf = P.build_templates(self.templates)
                elif cmd == P.CMD_ATTLOG_RRQ:
                    buf = P.build_attlog(self.punches)
                else:
                    reply(P.CMD_ACK_ERROR)
                    return out
                state["buffer"] = buf
                reply(P.CMD_ACK_OK, b"\x00" + struct.pack("<I", len(buf)) + b"\x00" * 4)
            elif c == P.CMD_READ_BUFFER:
                start, size = struct.unpack("<ii", pkt.data[:8])
                part = state.get("buffer", b"")[start:start + size]
                self._send_large(reply, part)
            elif c == P.CMD_FREE_DATA:
                state.pop("buffer", None)
                state["upload"] = b""
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_PREPARE_DATA:
                state["upload"] = b""
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_DATA:
                state["upload"] = state.get("upload", b"") + pkt.data
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_SAVE_USERTEMPS:
                self._save_usertemps(state.get("upload", b""))
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_USER_WRQ:
                users, _ = P.parse_users(struct.pack("<I", len(pkt.data)) + pkt.data, 1)
                for u in users:
                    self._put_user(u)
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_DELETE_USER:
                uid = struct.unpack("<h", pkt.data[:2])[0]
                self.users = [u for u in self.users if u.uid != uid]
                self.templates = [t for t in self.templates if t.uid != uid]
                self.faces.pop(uid, None)
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_DELETE_USERTEMP:
                uid, fid = struct.unpack("<hb", pkt.data[:3])
                self.templates = [t for t in self.templates if not (t.uid == uid and t.fid == fid)]
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_CLEAR_DATA:
                self.users, self.templates, self.faces, self.punches = [], [], {}, []
                reply(P.CMD_ACK_OK)
            elif c == P.CMD_GET_USERTEMP:
                uid, fid = struct.unpack("<hb", pkt.data[:3])
                face = self.faces.get(uid) if fid == P.FACE_FID else None
                if face:
                    self._send_large(reply, face)
                else:
                    reply(P.CMD_ACK_ERROR)
            elif c == P.CMD_CLEAR_ATTLOG:
                self.punches.clear()
                reply(P.CMD_ACK_OK)
            else:  # enable/disable/refresh/voice...
                reply(P.CMD_ACK_OK)
        return out

    @staticmethod
    def _send_large(reply, part: bytes) -> None:
        reply(P.CMD_PREPARE_DATA, struct.pack("<I", len(part)))
        for i in range(0, len(part), 1000):
            reply(P.CMD_DATA, part[i:i + 1000])
        reply(P.CMD_ACK_OK)

    def _put_user(self, u: P.DevUser) -> None:
        self.users = [x for x in self.users if x.uid != u.uid] + [u]
        self.users.sort(key=lambda x: x.uid)

    def _save_usertemps(self, buf: bytes) -> None:
        ulen, tlen, flen = struct.unpack("<III", buf[:12])
        upack, table, fpack = buf[12:12 + ulen], buf[12 + ulen:12 + ulen + tlen], buf[12 + ulen + tlen:]
        rec = 73 if self.user_size == 72 else 29
        for off in range(0, len(upack), rec):
            r = upack[off:off + rec]
            if rec == 73:
                _f, uid, pri, pw, name, card, _z, grp, pin = struct.unpack("<BHB8s24sIB7sx24s", r)
                self._put_user(P.DevUser(uid, P.cstr(pin), P.cstr(name), pri, P.cstr(pw), card, P.cstr(grp)))
        for off in range(0, len(table), 8):
            _f, uid, fid, start = struct.unpack("<bHbI", table[off:off + 8])
            size = struct.unpack("<H", fpack[start:start + 2])[0]
            fid -= 0x10
            self.templates = [t for t in self.templates if not (t.uid == uid and t.fid == fid)]
            self.templates.append(P.DevTemplate(uid, fid, 1, fpack[start + 2:start + 2 + size]))


class _TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        term: FakeTerminal = self.server.terminal
        state: dict = {}
        buf = b""
        while not state.get("closed"):
            try:
                chunk = self.request.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while len(buf) >= 8:
                size = struct.unpack("<I", buf[4:8])[0]
                if len(buf) < 8 + size:
                    break
                pkt, buf = buf[8:8 + size], buf[8 + size:]
                for o in term.handle(state, pkt):
                    self.request.sendall(P.tcp_wrap(o))


class _UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        states = self.server.states
        state = states.setdefault(self.client_address, {})
        for o in self.server.terminal.handle(state, data):
            sock.sendto(o, self.client_address)


class _TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _UDP(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(terminal: FakeTerminal, host: str = "127.0.0.1", port: int = 0, udp: bool = True):
    """Start in background threads. Returns (port, stop())."""
    tcp = _TCP((host, port), _TCPHandler)
    tcp.terminal = terminal
    port = tcp.server_address[1]
    servers = [tcp]
    if udp:
        u = _UDP((host, port), _UDPHandler)
        u.terminal, u.states = terminal, {}
        servers.append(u)
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()

    def stop():
        for s in servers:
            s.shutdown()
            s.server_close()
    return port, stop


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=4370)
    ap.add_argument("--key", type=int, default=0)
    ap.add_argument("--sn", default="SIMT4370001")
    a = ap.parse_args()
    port, _ = serve(FakeTerminal(a.sn, a.key).seed(5, 20), a.host, a.port)
    print(f"fake terminal {a.sn} on {a.host}:{port} (TCP+UDP). Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
