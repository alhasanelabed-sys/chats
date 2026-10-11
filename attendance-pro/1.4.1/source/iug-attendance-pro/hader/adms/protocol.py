"""Pure helpers for the ADMS / PUSH text protocol.

The terminal (SpeedFace-V5L, ProFace X, MB/UFace series...) is the HTTP client:

  GET  /iclock/cdata?SN=..&options=all&pushver=..   -> option block (handshake)
  POST /iclock/cdata?SN=..&table=ATTLOG&Stamp=..     -> punches
  POST /iclock/cdata?SN=..&table=OPERLOG&Stamp=..    -> users, templates, op-logs
  POST /iclock/cdata?SN=..&table=BIODATA|ATTPHOTO|options|ERRORLOG ...
  GET  /iclock/getrequest?SN=..[&INFO=..]            -> heartbeat, gets "C:<id>:<cmd>"
  POST /iclock/devicecmd?SN=..                       -> "ID=..&Return=..&CMD=.."
  POST /iclock/querydata?SN=..&tablename=..          -> answers to DATA QUERY tablename=
  GET  /iclock/rtdata?SN=..&type=time                -> server time

Nothing in here touches the database, which keeps it easy to unit-test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl

VERIFY_TYPES = {
    0: "password", 1: "fingerprint", 2: "card", 3: "password", 4: "card", 5: "fp_pw",
    6: "fp_card", 7: "pw_card", 8: "pin_fp", 9: "fp_pw", 10: "fp_card", 11: "pw_card",
    12: "fp_pw_card", 13: "pin_fp_pw", 14: "fp_card_pin", 15: "face", 16: "face_fp",
    17: "face_pw", 18: "face_card", 19: "face_fp_card", 20: "face_fp_pw", 21: "finger_vein",
    25: "palm", 26: "palm_card", 27: "palm_face", 28: "palm_fp", 29: "palm_face_fp",
    200: "other", 255: "other",
}


# --------------------------------------------------------------------------
# Low-level parsing
# --------------------------------------------------------------------------

def decode_body(raw: bytes) -> str:
    """Devices send UTF-8 (new firmware) or a legacy code page for names. Decoded line by
    line, so one name typed in the old code page does not garble every other line."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    out = []
    for line in raw.split(b"\n"):
        for enc in ("utf-8", "cp1256", "latin-1"):
            try:
                out.append(line.decode(enc))
                break
            except UnicodeDecodeError:
                continue
    return "\n".join(out)


def parse_kv(text: str, sep: str = "\t") -> dict[str, str]:
    """``PIN=1\\tName=Ali\\tCard=`` -> {"pin": "1", "name": "Ali", "card": ""}.

    Keys are lower-cased because firmware versions disagree (Pin/PIN, Tmp/TMP).
    Values keep any '=' they contain (base64 templates end with '=').
    """
    out: dict[str, str] = {}
    for part in text.split(sep):
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key.strip().lower()] = value.strip()
    return out


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_time(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class AttRecord:
    pin: str
    time: datetime
    state: int = 0
    verify: int = 0
    work_code: str = ""
    mask: int | None = None
    temperature: float | None = None


@dataclass
class AttLine:
    """A nonempty uploaded line, including diagnostics for rejected records.

    ``record`` retains the legacy parser's interpretation so ATTLOG reply counts
    remain compatible with terminals even when stricter ingestion rejects it.
    """
    line: int
    record: AttRecord | None = None
    reason: str | None = None
    pin: str = ""


def parse_attlog_detailed(body: str) -> list[AttLine]:
    """ATTLOG lines: PIN \\t Time \\t Status \\t Verify \\t WorkCode \\t Reserved \\t Reserved
    [\\t MaskFlag \\t Temperature] (the last two on thermal/mask SpeedFace models)."""
    out: list[AttLine] = []
    for line_number, line in enumerate(body.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.rstrip("\r").split("\t")
        if len(parts) < 2:
            out.append(AttLine(line_number, reason="missing_fields"))
            continue
        if not parts[0].strip():
            out.append(AttLine(line_number, reason="missing_employee_code"))
            continue
        ts = parse_time(parts[1])
        if ts is None:
            out.append(AttLine(line_number, reason="invalid_timestamp", pin=parts[0].strip()))
            continue
        rec = AttRecord(pin=parts[0].strip(), time=ts,
                        state=_int(parts[2] if len(parts) > 2 else 0),
                        verify=_int(parts[3] if len(parts) > 3 else 0),
                        work_code=(parts[4].strip() if len(parts) > 4 else ""))
        if len(parts) > 8:
            try:
                temp = float(parts[8])
                if 25.0 <= temp <= 45.0:
                    rec.temperature = temp
                    rec.mask = _int(parts[7], 0)
            except ValueError:
                pass
        reason = None
        for index, field_name in ((2, "state"), (3, "verify")):
            if len(parts) > index and parts[index].strip():
                try:
                    value = int(parts[index].strip())
                    if not 0 <= value <= 255:
                        reason = "invalid_" + field_name
                except ValueError:
                    reason = "invalid_" + field_name
                if reason:
                    break
        if reason is None and len(rec.work_code) > 20:
            reason = "invalid_work_code"
        out.append(AttLine(line_number, record=rec, reason=reason, pin=rec.pin))
    return out


def parse_attlog(body: str) -> list[AttRecord]:
    """Compatibility parser; detailed diagnostics are available to the inbox."""
    return [line.record for line in parse_attlog_detailed(body) if line.record is not None]


@dataclass
class OperItem:
    kind: str                 # USER, FP, FACE, BIODATA, USERPIC, BIOPHOTO, OPLOG, WORKCODE ...
    data: dict[str, str] = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)  # positional values for OPLOG
    raw: str = ""             # retained for rejected-upload diagnostics


_OPER_RE = re.compile(r"^([A-Z_]+)\s+(.*)$", re.S)


def parse_operlog(body: str) -> list[OperItem]:
    """OPERLOG bodies mix several record kinds, one per line::

        USER PIN=1\\tName=Ali\\tPri=0\\tPasswd=\\tCard=\\tGrp=1\\tTZ=..\\tVerify=0
        FP PIN=1\\tFID=6\\tSize=1180\\tValid=1\\tTMP=TV...
        BIODATA Pin=1\\tNo=0\\tIndex=0\\tValid=1\\tDuress=0\\tType=9\\tMajorVer=39\\t...\\tTmp=..
        OPLOG 4\\t0\\t2024-01-01 10:00:00\\t0\\t0\\t0\\t0
    """
    items: list[OperItem] = []
    for line in body.splitlines():
        line = line.strip("\r\n\x00")
        if not line.strip():
            continue
        m = _OPER_RE.match(line.lstrip())
        if not m:
            continue
        kind, rest = m.group(1).upper(), m.group(2)
        if kind == "OPLOG":
            items.append(OperItem(kind, fields=rest.split("\t"), raw=line))
        else:
            items.append(OperItem(kind, data=parse_kv(rest), raw=line))
    return items


def parse_bio_lines(body: str, table: str = "BIODATA") -> list[OperItem]:
    """table=BIODATA / USERINFO bodies. Lines may or may not carry the prefix."""
    kind = {"USERINFO": "USER", "USER": "USER", "FINGERTMP": "FP"}.get(table.upper(), table.upper())
    items = []
    for line in body.splitlines():
        prefixed = parse_operlog(line)
        if prefixed:
            items.extend(prefixed)
        elif "=" in line:
            items.append(OperItem(kind, data=parse_kv(line), raw=line))
    return items


def parse_querydata(body: str) -> list[tuple[str, dict[str, str]]]:
    """/iclock/querydata lines: ``user uid=1\\tpin=1\\tname=Ali ...`` / ``biodata pin=1 ...``."""
    out = []
    for line in body.splitlines():
        line = line.strip("\r\n\x00")
        if not line.strip():
            continue
        name, _, rest = line.strip().partition(" ")
        out.append((name.lower(), parse_kv(rest)))
    return out


_OPT_SPLIT = re.compile(r"[,\n\r]+(?=~?[A-Za-z][\w.~]*=)")


def parse_options(body: str) -> dict[str, str]:
    """table=options / INFO replies: ``~DeviceName=SpeedFace-V5L,MAC=..,FWVersion=..``."""
    out: dict[str, str] = {}
    for part in _OPT_SPLIT.split(body.strip()):
        key, sep, value = part.strip().partition("=")
        if sep:
            out[key.strip().lstrip("~")] = value.strip().rstrip(",")
    return out


def parse_info_param(info: str) -> dict[str, str]:
    """getrequest ``INFO=Ver 8.0.4.2,UserCount,FPCount,AttCount,IP,FPVer,FaceVer,FaceTmpCnt,FaceCount,Fun``"""
    names = ["firmware", "user_count", "fp_count", "att_count", "ip", "fp_alg", "face_alg",
             "face_tmp_count", "face_count", "functions"]
    return {k: v.strip() for k, v in zip(names, info.split(","))}


@dataclass
class CmdReturn:
    id: int
    ret: int
    cmd: str
    extra: dict[str, str] = field(default_factory=dict)
    raw: str = ""


def parse_devicecmd(body: str) -> list[CmdReturn]:
    """``ID=12&Return=0&CMD=DATA`` lines. Lines that follow a return and do not
    start with ID= belong to it (the INFO command sends key=value lines)."""
    out: list[CmdReturn] = []
    for line in body.splitlines():
        line = line.strip("\r\n\x00")
        if not line.strip():
            continue
        if line.startswith("ID="):
            q = {k.lower(): v for k, v in parse_qsl(line, keep_blank_values=True)}
            out.append(CmdReturn(id=_int(q.get("id"), -1), ret=_int(q.get("return"), -1000),
                                 cmd=q.get("cmd", ""), raw=line))
        elif out:
            out[-1].extra.update(parse_options(line))
            out[-1].raw += "\n" + line
    return out


def parse_attphoto(raw: bytes) -> tuple[dict[str, str], bytes]:
    """ATTPHOTO body = text header ``PIN=20240101083000-7.jpg\\nSN=..\\nsize=..\\nCMD=uploadphoto``
    followed by a NUL and the JPEG bytes."""
    marker = raw.find(b"CMD=uploadphoto")
    if marker < 0:
        cut = raw.find(b"\x00")
    else:
        cut = raw.find(b"\x00", marker)
        if cut < 0:
            cut = marker + len(b"CMD=uploadphoto")
    header = raw[:cut].decode("latin-1") if cut >= 0 else ""
    data = raw[cut + 1:] if cut >= 0 else raw
    meta = {}
    for line in header.splitlines():
        k, _, v = line.partition("=")
        meta[k.strip().lower()] = v.strip()
    return meta, data


# --------------------------------------------------------------------------
# Server -> device
# --------------------------------------------------------------------------

def dev_encode_time(dt: datetime) -> int:
    """The compact integer time used by the terminal firmware (SET OPTION DateTime / rtdata)."""
    return ((((dt.year % 100) * 12 * 31 + (dt.month - 1) * 31 + dt.day - 1) * 24 * 60 * 60)
            + (dt.hour * 60 + dt.minute) * 60 + dt.second)


def dev_decode_time(value: int) -> datetime:
    second = value % 60; value //= 60
    minute = value % 60; value //= 60
    hour = value % 24; value //= 24
    day = value % 31 + 1; value //= 31
    month = value % 12 + 1; value //= 12
    return datetime(value + 2000, month, day, hour, minute, second)


def _stamp(value: str | None) -> str:
    return "None" if value in (None, "", "0", "None") else str(value)


def option_block(sn: str, *, att_stamp: str, op_stamp: str, photo_stamp: str, time_zone: int,
                 delay: int, trans_interval: int, trans_times: str, realtime: bool,
                 upload_photos: bool, server_ver: str) -> str:
    trans_flag = ["AttLog", "OpLog", "EnrollUser", "ChgUser", "EnrollFP", "ChgFP",
                  "UserPic", "FACE", "WORKCODE", "BioPhoto"]
    if upload_photos:
        trans_flag.insert(2, "AttPhoto")
    lines = [
        f"GET OPTION FROM: {sn}",
        # "None" = the server has nothing yet: the terminal uploads everything it holds.
        f"ATTLOGStamp={_stamp(att_stamp)}",
        f"OPERLOGStamp={_stamp(op_stamp)}",
        f"ATTPHOTOStamp={_stamp(photo_stamp)}",
        "ErrorDelay=30",
        f"Delay={delay}",
        f"TransTimes={trans_times}",
        f"TransInterval={trans_interval}",
        "TransFlag=TransData " + "\t".join(trans_flag),
        f"TimeZone={time_zone}",
        f"Realtime={1 if realtime else 0}",
        "Encrypt=None",
        f"ServerVer={server_ver}",
        f"PushProtVer={server_ver}",
        "PushOptionsFlag=1",
        "PushOptions=FingerFunOn,FaceFunOn,FPVersion,FaceVersion,MultiBioDataSupport,"
        "MultiBioPhotoSupport,MultiBioVersion,UserCount,FPCount,FaceCount,PvCount,TransactionCount,"
        "MaxUserCount,MaxAttLogCount,IPAddress,MAC,FirmVer,DeviceName",
        # Which BIODATA types the server stores: 1 fingerprint, 2 NIR face, 7 finger vein,
        # 8 palm, 9 visible-light face (SpeedFace).
        "MultiBioDataSupport=0:1:1:0:0:0:0:1:1:1",
        "MultiBioPhotoSupport=0:0:0:0:0:0:0:0:0:1",
    ]
    return "\n".join(lines) + "\n"


def tsv(**fields) -> str:
    """Build ``Key=Value\\tKey=Value`` — tabs/newlines inside values would break the line."""
    return "\t".join(f"{k}={str(v).replace(chr(9), ' ').replace(chr(10), ' ')}" for k, v in fields.items())
