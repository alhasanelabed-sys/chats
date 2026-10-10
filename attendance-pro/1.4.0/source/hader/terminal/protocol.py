"""The "standalone" protocol spoken by terminals on port 4370 (TCP or UDP).

Pure Python, no third-party package: this is the same wire format the vendor
standalone SDK uses. A packet is::

    [TCP only: 50 50 82 7D <uint32 length>]  <cmd:u16> <checksum:u16> <session:u16> <reply:u16> <data>

Everything here is pure encoding/decoding so it can be tested without a device
(see ``tools/terminal_simulator.py``, which speaks the device side).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta

USHRT_MAX = 65535
TCP_MAGIC = b"\x50\x50\x82\x7d"

# Commands
CMD_DB_RRQ = 7
CMD_USER_WRQ = 8
CMD_USERTEMP_RRQ = 9
CMD_OPTIONS_RRQ = 11
CMD_OPTIONS_WRQ = 12
CMD_ATTLOG_RRQ = 13
CMD_CLEAR_DATA = 14
CMD_CLEAR_ATTLOG = 15
CMD_DELETE_USER = 18
CMD_DELETE_USERTEMP = 19
CMD_GET_FREE_SIZES = 50
CMD_STARTENROLL = 61
CMD_CANCELCAPTURE = 62
CMD_GET_USERTEMP = 88
CMD_SAVE_USERTEMPS = 110
CMD_DEL_USER_TEMP = 134
CMD_GET_TIME = 201
CMD_SET_TIME = 202
CMD_REG_EVENT = 500
CMD_CONNECT = 1000
CMD_EXIT = 1001
CMD_ENABLEDEVICE = 1002
CMD_DISABLEDEVICE = 1003
CMD_RESTART = 1004
CMD_REFRESHDATA = 1013
CMD_REFRESHOPTION = 1014
CMD_TESTVOICE = 1017
CMD_GET_VERSION = 1100
CMD_AUTH = 1102
CMD_PREPARE_DATA = 1500
CMD_DATA = 1501
CMD_FREE_DATA = 1502
CMD_DATA_WRRQ = 1503
CMD_READ_BUFFER = 1504
CMD_ACK_OK = 2000
CMD_ACK_ERROR = 2001
CMD_ACK_DATA = 2002
CMD_ACK_RETRY = 2003
CMD_ACK_REPEAT = 2004
CMD_ACK_UNAUTH = 2005

# Tables for CMD_DB_RRQ / CMD_DATA_WRRQ
FCT_ATTLOG = 1
FCT_FINGERTMP = 2
FCT_OPLOG = 4
FCT_USER = 5

# Real-time events (CMD_REG_EVENT)
EF_ATTLOG = 1
EF_FINGER = 1 << 1
EF_ENROLLUSER = 1 << 2
EF_ENROLLFINGER = 1 << 3

FACE_FID = 50  # near-infrared face templates are stored as "finger" 50 on near-infrared face terminals

OK_REPLIES = (CMD_ACK_OK, CMD_PREPARE_DATA, CMD_DATA, CMD_ACK_DATA)


# --------------------------------------------------------------------------
# Packets
# --------------------------------------------------------------------------

def checksum(buf: bytes) -> int:
    """16-bit one's-complement style checksum used by the firmware."""
    if len(buf) % 2:
        buf += b"\x00"
    total = 0
    for (word,) in struct.iter_unpack("<H", buf):
        total += word
        if total > USHRT_MAX:
            total -= USHRT_MAX
    total = ~total
    while total < 0:
        total += USHRT_MAX
    return total & 0xFFFF


def build_packet(command: int, session: int, reply: int, data: bytes = b"") -> bytes:
    """One packet body (without the TCP envelope).

    Byte-exact with the vendor SDK: the checksum covers the *previous* sequence
    number and the header carries the next one (``reply`` is the previous one)."""
    chk = checksum(struct.pack("<4H", command, 0, session, reply) + data)
    return struct.pack("<4H", command, chk, session, next_reply(reply)) + data


def tcp_wrap(packet: bytes) -> bytes:
    return TCP_MAGIC + struct.pack("<I", len(packet)) + packet


@dataclass
class Packet:
    command: int
    checksum: int
    session: int
    reply: int
    data: bytes = b""

    @property
    def ok(self) -> bool:
        return self.command in OK_REPLIES


def parse_packet(raw: bytes) -> Packet:
    if len(raw) < 8:
        raise ValueError("short packet")
    cmd, chk, session, reply = struct.unpack("<4H", raw[:8])
    return Packet(cmd, chk, session, reply, raw[8:])


def next_reply(reply: int) -> int:
    reply += 1
    return reply - USHRT_MAX if reply >= USHRT_MAX else reply


def make_commkey(key: int, session: int, ticks: int = 50) -> bytes:
    """Answer to CMD_ACK_UNAUTH for a terminal with a communication key (password)."""
    k = 0
    for i in range(32):
        k = (k << 1 | 1) if key & (1 << i) else k << 1
    k = (k + session) & 0xFFFFFFFF
    b = struct.pack("<I", k)
    b = bytes((b[0] ^ ord("Z"), b[1] ^ ord("K"), b[2] ^ ord("S"), b[3] ^ ord("O")))
    lo, hi = struct.unpack("<HH", b)
    b = struct.pack("<HH", hi, lo)
    t = ticks & 0xFF
    return bytes((b[0] ^ t, b[1] ^ t, t, b[3] ^ t))


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------

def decode_time(raw: bytes) -> datetime:
    t = struct.unpack("<I", raw[:4])[0]
    second = t % 60
    t //= 60
    minute = t % 60
    t //= 60
    hour = t % 24
    t //= 24
    day = t % 31 + 1
    t //= 31
    month = t % 12 + 1
    t //= 12
    return datetime(t + 2000, month, day, hour, minute, second)


def encode_time(d: datetime) -> int:
    return (((d.year % 100) * 12 * 31 + (d.month - 1) * 31 + d.day - 1) * 86400
            + (d.hour * 60 + d.minute) * 60 + d.second)


def decode_hex_time(raw: bytes) -> datetime:
    """6-byte y m d H M S used in real-time events."""
    y, mo, d, h, mi, s = struct.unpack("6B", raw[:6])
    return datetime(y + 2000, mo, d, h, mi, s)


def _utf8_cut_tail(raw: bytes, start: int) -> bool:
    """True when raw[start:] is the beginning of a UTF-8 letter cut by the end of the field
    (a lead byte followed only by continuation bytes, fewer than the letter needs)."""
    tail = raw[start:]
    if not tail or start == 0:
        return False
    lead = tail[0]
    need = 2 if 0xC2 <= lead <= 0xDF else 3 if 0xE0 <= lead <= 0xEF else 4 if 0xF0 <= lead <= 0xF4 else 0
    return bool(need) and len(tail) < need and all(0x80 <= b <= 0xBF for b in tail[1:])


def cstr(raw: bytes, encoding: str = "utf-8") -> str:
    """Text field of a terminal record (name, PIN...). Terminals store UTF-8 on current
    firmware and Windows-1256 on older Arabic firmware; a 24-byte name field often cuts the
    last Arabic letter in half, so only that incomplete tail is dropped."""
    raw = raw.split(b"\x00", 1)[0]
    for enc in dict.fromkeys((encoding, "utf-8")):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError as exc:
            if enc.replace("-", "").lower() == "utf8" and _utf8_cut_tail(raw, exc.start):
                try:
                    return raw[:exc.start].decode(enc).strip()
                except UnicodeDecodeError:
                    pass
    try:
        return raw.decode("cp1256").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def repair_mojibake(text: str) -> str:
    """Undo names stored by an earlier version with the wrong code page
    (e.g. 'ÚÈÏ' or 'Ù…Ø­Ù…Ø¯' instead of Arabic)."""
    if not text or not any("\u0080" <= ch <= "\u00ff" for ch in text):
        return text
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        try:
            raw = text.encode("cp1252")
        except UnicodeEncodeError:
            return text
    fixed = cstr(raw)
    return fixed if fixed != text and any("\u0600" <= ch <= "\u06ff" for ch in fixed) else text


def parse_option_reply(data: bytes) -> tuple[str, str]:
    """``~SerialNumber=ABC\\0`` -> ("SerialNumber", "ABC")."""
    text = cstr(data, "latin-1")
    key, _, value = text.partition("=")
    return key.lstrip("~"), value


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class DevUser:
    uid: int                 # internal slot number on the terminal
    user_id: str             # the PIN / employee code
    name: str = ""
    privilege: int = 0       # 0 user, 14 admin (3 on some models)
    password: str = ""
    card: int = 0
    group_id: str = "1"


@dataclass
class DevTemplate:
    uid: int
    fid: int                 # finger 0..9; 50 = near-infrared face
    valid: int
    template: bytes
    user_id: str = ""


@dataclass
class DevPunch:
    user_id: str
    time: datetime
    state: int = 0           # check-in/out/break... ("punch state")
    verify: int = 0          # 1 finger, 15 face, 25 palm, 4 card, 3 password...
    work_code: str = ""
    uid: int = 0


@dataclass
class Sizes:
    users: int = 0
    fingers: int = 0
    records: int = 0
    faces: int = 0
    users_cap: int = 0
    fingers_cap: int = 0
    records_cap: int = 0
    faces_cap: int = 0
    extra: dict = field(default_factory=dict)


def parse_sizes(data: bytes) -> Sizes:
    s = Sizes()
    if len(data) >= 80:
        f = struct.unpack("<20i", data[:80])
        s.users, s.fingers, s.records = f[4], f[6], f[8]
        s.fingers_cap, s.users_cap, s.records_cap = f[14], f[15], f[16]
        rest = data[80:]
        if len(rest) >= 12:
            g = struct.unpack("<3i", rest[:12])
            s.faces, s.faces_cap = g[0], g[2]
    return s


def build_sizes(s: Sizes) -> bytes:
    f = [0] * 20
    f[4], f[6], f[8] = s.users, s.fingers, s.records
    f[14], f[15], f[16] = s.fingers_cap, s.users_cap, s.records_cap
    return struct.pack("<20i", *f) + struct.pack("<3i", s.faces, 0, s.faces_cap)


USER28 = "<HB5s8sIxBhI"
USER72 = "<HB8s24sIx7sx24s"


_PIN_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_\-]{0,23}$")


def valid_pin(pin: str) -> bool:
    """A personnel number as terminals store it (digits / latin letters). Anything else
    means the record was decoded with the wrong layout and must not become an employee."""
    return bool(_PIN_RE.fullmatch(pin or ""))


def _layouts(total: int, count: int, sizes: tuple[int, ...]) -> list[int]:
    """Candidate record sizes, most likely first: the one matching the announced count,
    then those that divide the data exactly, then the rest."""
    exact = [total // count] if count and total % count == 0 and total // count in sizes else []
    fits = [x for x in sizes if total % x == 0]
    return list(dict.fromkeys(exact + fits + list(sizes)))


def _pick(layouts, decode):
    """First layout whose records are (almost) all valid; else the best one."""
    best = (-1.0, None)
    for size in layouts:
        good, n = decode(size)
        score = len(good) / n if n else 0.0
        if score >= 0.9:
            return size, good
        if score > best[0]:
            best = (score, (size, good))
    return best[1] if best[1] else (layouts[0], [])


def _decode_users(body: bytes, size: int, encoding: str) -> list[DevUser]:
    users = []
    for off in range(0, len(body) - size + 1, size):
        rec = body[off:off + size]
        if size == 28:
            uid, pri, pw, name, card, group, _tz, uid_num = struct.unpack(USER28, rec)
            user_id, group_id = str(uid_num), str(group)
        else:
            uid, pri, pw, name, card, group, user_id = struct.unpack(USER72, rec)
            user_id, group_id = cstr(user_id, encoding), cstr(group, encoding) or "1"
        users.append(DevUser(uid=uid, user_id=user_id or str(uid), name=cstr(name, encoding), privilege=pri,
                             password=cstr(pw, encoding), card=card, group_id=group_id))
    return users


def parse_users(data: bytes, count: int = 0, encoding: str = "utf-8") -> tuple[list[DevUser], int]:
    """Users table (``CMD_USERTEMP_RRQ``/``FCT_USER``). Returns (users, record size).

    The layout (28 or 72 bytes) is the one whose records decode to valid personnel
    numbers; records that still do not are dropped instead of creating junk people."""
    if len(data) < 4:
        return [], 72
    total = struct.unpack("<I", data[:4])[0]
    body = data[4:4 + total]
    def decode(size):
        users = _decode_users(body, size, encoding)
        return [u for u in users if valid_pin(u.user_id)], len(users)
    size, good = _pick(_layouts(total, count, (72, 28)), decode)
    return good, size


def pack_user(u: DevUser, size: int, encoding: str = "utf-8") -> bytes:
    """Body of ``CMD_USER_WRQ``."""
    name = u.name.encode(encoding, "replace")
    if size == 28:
        return struct.pack(USER28, u.uid, u.privilege, u.password.encode()[:5], name[:8], u.card,
                           int(u.group_id or 1) if str(u.group_id).isdigit() else 1, 0, int(u.user_id))
    return struct.pack(USER72, u.uid, u.privilege, u.password.encode()[:8], name[:24], u.card,
                       str(u.group_id or "1").encode()[:7], u.user_id.encode()[:24])


def build_users(users: list[DevUser], size: int = 72) -> bytes:
    body = b"".join(pack_user(u, size) for u in users)
    return struct.pack("<I", len(body)) + body


def parse_templates(data: bytes) -> list[DevTemplate]:
    """Fingerprint table (``CMD_DB_RRQ``/``FCT_FINGERTMP``)."""
    if len(data) < 4:
        return []
    total = struct.unpack("<i", data[:4])[0]
    body = data[4:4 + total]
    out, off = [], 0
    while off + 6 <= len(body):
        size, uid, fid, valid = struct.unpack("<HHbb", body[off:off + 6])
        if size < 6:
            break
        out.append(DevTemplate(uid=uid, fid=fid, valid=valid, template=body[off + 6:off + size]))
        off += size
    return out


def build_templates(templates: list[DevTemplate]) -> bytes:
    body = b"".join(struct.pack("<HHbb", len(t.template) + 6, t.uid, t.fid, t.valid) + t.template
                    for t in templates)
    return struct.pack("<i", len(body)) + body


def _attlog_record(rec: bytes, size: int, uid_to_pin: dict[int, str]) -> DevPunch | None:
    """One record, or None when it does not decode to a plausible punch."""
    try:
        if size == 8:
            uid, verify, ts, state = struct.unpack("<HB4sB", rec)
            p = DevPunch(uid_to_pin.get(uid, str(uid)), decode_time(ts), state, verify, uid=uid)
        elif size == 16:
            user_id, ts, verify, state, _r, wc = struct.unpack("<I4sBB2sI", rec)
            p = DevPunch(str(user_id), decode_time(ts), state, verify, str(wc or ""))
        else:
            uid, user_id, verify, ts, state, wc = struct.unpack("<H24sB4sB8s", rec)
            pin = cstr(user_id) or uid_to_pin.get(uid, str(uid))
            p = DevPunch(pin, decode_time(ts), state, verify, cstr(wc), uid=uid)
    except (ValueError, struct.error):  # impossible date: wrong layout or a damaged record
        return None
    if valid_pin(p.user_id) and datetime(2010, 1, 1) <= p.time <= datetime.now() + timedelta(days=2):
        return p
    return None


def _decode_attlog(body: bytes, size: int, uid_to_pin: dict[int, str], start: int = 0,
                   resync: bool = True) -> tuple[list[DevPunch], int]:
    """-> (valid punches, number of record slots). When a record is damaged the decoder
    looks for the next position where two consecutive records are valid again, so one
    bad record (or a lost / extra byte in the stream) never shifts all the following ones."""
    out, n, pos, end = [], 0, start, len(body)
    while pos + size <= end:
        p = _attlog_record(body[pos:pos + size], size, uid_to_pin)
        n += 1
        if p is not None:
            out.append(p)
            pos += size
            continue
        if not resync:
            pos += size
            continue
        next_pos = pos + size  # every path must advance, including a damaged final record
        for shift in range(1, 2 * size):
            q = pos + shift
            if q + 2 * size > end:
                break
            if _attlog_record(body[q:q + size], size, uid_to_pin) and \
                    _attlog_record(body[q + size:q + 2 * size], size, uid_to_pin):
                next_pos = q
                break
        pos = next_pos
    return out, n


LAST_ATTLOG: dict = {}  # layout / record / valid counts of the last parse (diagnostics)


def parse_attlog(data: bytes, count: int = 0, uid_to_pin: dict[int, str] | None = None,
                 diagnostics: dict | None = None) -> list[DevPunch]:
    """Attendance log (``CMD_ATTLOG_RRQ``). Firmware uses 8, 16 or 40-byte records; the
    layout that yields valid punches wins (the record count announced by the terminal can
    be off when someone punches during the read). Invalid records are dropped."""
    if len(data) < 4:
        stats = dict(size=40, start=0, records=0, valid=0, rejected=0,
                     declared_bytes=0, received_bytes=0, truncated=bool(data), clean=not data)
        LAST_ATTLOG.clear()
        LAST_ATTLOG.update(stats)
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(stats)
        return []
    total = struct.unpack("<I", data[:4])[0]
    body = data[4:4 + total]
    # layout and start offset: score a sample of every candidate, then decode everything
    best = (-1.0, 40, 0)
    sample = body[:40 * 200]
    for size in _layouts(total, count, (40, 16, 8)):
        for start in range(size):
            good, n = _decode_attlog(sample, size, uid_to_pin or {}, start, resync=False)
            score = len(good) / n if n else 0.0
            if score > best[0] + 1e-9:
                best = (score, size, start)
            if start == 0 and score >= 0.98:
                break
        if best[0] >= 0.98:
            break
    _score, size, start = best
    good, n = _decode_attlog(body, size, uid_to_pin or {}, start)
    stats = dict(size=size, start=start, records=len(body) // size, valid=len(good),
                 rejected=n - len(good), declared_bytes=total, received_bytes=len(body),
                 truncated=len(data) - 4 < total,
                 clean=(start == 0 and n == len(good) and len(body) % size == 0 and len(data) - 4 == total))
    LAST_ATTLOG.clear()
    LAST_ATTLOG.update(stats)
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(stats)
    return good


def build_attlog(punches: list[DevPunch]) -> bytes:
    body = b"".join(struct.pack("<H24sB4sB8s", p.uid, p.user_id.encode(), p.verify,
                                struct.pack("<I", encode_time(p.time)), p.state, p.work_code.encode())
                    for p in punches)
    return struct.pack("<I", len(body)) + body


def build_usertemps(users: list[DevUser], templates: dict[int, list[DevTemplate]], user_size: int) -> bytes:
    """Buffer for ``CMD_SAVE_USERTEMPS``: users and their fingerprints in one transfer."""
    upack, table, fpack = b"", b"", b""
    for u in users:
        if user_size == 28:
            upack += struct.pack("<BHB5s8sIxBhI", 2, u.uid, u.privilege, u.password.encode()[:5],
                                 u.name.encode("utf-8", "replace")[:8], u.card,
                                 int(u.group_id) if str(u.group_id).isdigit() else 1, 0, int(u.user_id))
        else:
            upack += struct.pack("<BHB8s24sIB7sx24s", 2, u.uid, u.privilege, u.password.encode()[:8],
                                 u.name.encode("utf-8", "replace")[:24], u.card, 0,
                                 str(u.group_id or "1").encode()[:7], u.user_id.encode()[:24])
        for t in templates.get(u.uid, []):
            tfp = struct.pack("<H", len(t.template)) + t.template
            table += struct.pack("<bHbI", 2, u.uid, 0x10 + t.fid, len(fpack))
            fpack += tfp
    return struct.pack("<III", len(upack), len(table), len(fpack)) + upack + table + fpack


def parse_rt_attlog(data: bytes) -> DevPunch | None:
    """Real-time EF_ATTLOG event (sent while ``CMD_REG_EVENT`` is active)."""
    if len(data) >= 36:
        user_id = cstr(data[:24])
        verify, state = data[24], data[25]
        return DevPunch(user_id, decode_hex_time(data[26:32]), state, verify)
    if len(data) >= 12:
        user_id, verify, state = struct.unpack("<HBB", data[:4])
        return DevPunch(str(user_id), decode_hex_time(data[4:10]), state, verify)
    return None
