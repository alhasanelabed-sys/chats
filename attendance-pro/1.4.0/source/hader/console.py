"""Remote device panel: the terminal's own menu, driven from the program.

Terminals do not share their screen, so the panel rebuilds their menu here: every value
is read live from the terminal (port 4370) or from what it reported when it last
connected (port 90), and every change is written back to it.

Option names are the terminal's own. Not every firmware has every option: only the
ones the terminal answers are shown, and anything else it reported is listed under
"Advanced" with its raw name.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from . import models as m
from .db import session_scope

log = logging.getLogger("hader.console")

# (key or alternative keys, Arabic label, English label, kind)  kind: text | number | bool | ro
# Firmware families name some options differently; the first name the terminal answers is used.
SECTIONS = [
    {"id": "info", "icon": "ℹ", "ar": "حول الجهاز", "en": "About", "fields": [
        ("DeviceName", "الطراز", "Model", "ro"),
        ("SerialNumber", "الرقم التسلسلي", "Serial number", "ro"),
        ("FWVersion", "إصدار البرنامج الثابت", "Firmware", "ro"),
        ("Platform", "المنصة", "Platform", "ro"),
        ("MAC", "عنوان MAC", "MAC address", "ro"),
        ("ProductTime", "تاريخ التصنيع", "Manufactured", "ro"),
        (("ZKFPVersion", "FPVersion"), "خوارزمية البصمة", "Fingerprint algorithm", "ro"),
        (("ZKFaceVersion", "FaceVersion"), "خوارزمية الوجه", "Face algorithm", "ro"),
        ("PvVersion", "خوارزمية الكف", "Palm algorithm", "ro"),
        ("UserCount", "المستخدمون", "Users", "ro"),
        ("FPCount", "البصمات", "Fingerprints", "ro"),
        ("FaceCount", "الوجوه", "Faces", "ro"),
        ("PvCount", "الكف", "Palms", "ro"),
        ("TransactionCount", "سجلات الحضور", "Records", "ro"),
        ("MaxUserCount", "سعة المستخدمين", "User capacity", "ro"),
        ("MaxFingerCount", "سعة البصمات", "Fingerprint capacity", "ro"),
        ("MaxFaceCount", "سعة الوجوه", "Face capacity", "ro"),
        ("MaxAttLogCount", "سعة السجلات", "Record capacity", "ro"),
    ]},
    {"id": "time", "icon": "🕒", "ar": "التاريخ والوقت", "en": "Date & time", "fields": [
        ("DeviceTime", "وقت الجهاز الآن", "Terminal time", "ro"),
        ("DateFormat", "صيغة التاريخ", "Date format", "text"),
        ("TZAdj", "فرق التوقيت (ساعة)", "Time-zone offset (h)", "number"),
        (("NTPServer", "NtpServer", "SNTPServer"), "خادم الوقت (NTP)", "Time server (NTP)", "text"),
        (("DaylightSavingTimeOn", "DaylightSavingTime", "DLSTSupport"), "التوقيت الصيفي", "Daylight saving", "bool"),
    ]},
    {"id": "bio", "icon": "🖐", "ar": "دقة التعرف", "en": "Recognition", "fields": [
        ("MThreshold", "حد مطابقة البصمة \u20661:N\u2069", "Fingerprint 1:N threshold", "number"),
        ("VThreshold", "حد مطابقة البصمة \u20661:1\u2069", "Fingerprint 1:1 threshold", "number"),
        (("FaceMThr", "FaceMThreshold", "VLFaceMThr"), "حد مطابقة الوجه \u20661:N\u2069", "Face 1:N threshold", "number"),
        (("FaceVThr", "FaceVThreshold", "VLFaceVThr"), "حد مطابقة الوجه \u20661:1\u2069", "Face 1:1 threshold", "number"),
        (("PvMThr", "PvMThreshold", "PalmMThr"), "حد مطابقة الكف \u20661:N\u2069", "Palm 1:N threshold", "number"),
        (("PvVThr", "PvVThreshold", "PalmVThr"), "حد مطابقة الكف \u20661:1\u2069", "Palm 1:1 threshold", "number"),
        (("FaceRecognizeDistance", "FaceDistance", "RecognitionDistance"), "مسافة التعرف على الوجه", "Face recognition distance", "number"),
        (("FPRetry", "FingerRetry"), "محاولات البصمة", "Fingerprint retries", "number"),
        (("ImgQuality", "FaceQuality", "FaceImageQuality"), "جودة صورة الوجه", "Face image quality", "number"),
    ]},
    {"id": "anti", "icon": "🛡", "ar": "مكافحة الانتحال", "en": "Anti-spoofing", "fields": [
        (("LiveDetectionFunOn", "LiveDetection", "FaceLiveDetect", "IsSupportLiveDetect"), "كشف الوجه الحي", "Live face detection", "bool"),
        (("AntiFakeFunOn", "FaceAntiFake", "FakeFaceDetect", "AntiSpoofing"), "منع الصور والفيديو", "Block photos & videos", "bool"),
        (("LiveDetectThreshold", "LiveThreshold", "AntiFakeThreshold"), "حساسية كشف الانتحال", "Spoof detection level", "number"),
        (("IRLiveDetect", "NIRLiveDetect", "IRAntiFake"), "كشف حي بالأشعة تحت الحمراء", "Infrared liveness", "bool"),
        (("PvLiveDetect", "PalmLiveDetect"), "كشف الكف الحي", "Live palm detection", "bool"),
        (("FakeFingerFunOn", "FingerAntiFake", "LFDFunOn"), "كشف البصمة المزيفة", "Fake finger detection", "bool"),
        (("MaskDetectionFunOn", "EnalbeMaskDetection", "EnableMaskDetection"), "كشف الكمامة", "Mask detection", "bool"),
        (("IRTempDetectionFunOn", "TempDetection"), "قياس الحرارة", "Temperature check", "bool"),
    ]},
    {"id": "attendance", "icon": "✅", "ar": "الحضور", "en": "Attendance", "fields": [
        (("AlarmReRec", "AttLogDupTime", "DuplicatePunchPeriod"), "فترة منع التكرار (دقيقة)", "Duplicate punch period (min)", "number"),
        (("ShowState", "AutoState", "AttState"), "إظهار حالة الحضور", "Show punch state", "bool"),
        (("AlarmAttLog", "AttLogAlarm"), "تنبيه امتلاء السجلات عند بقاء", "Warn when records left", "number"),
        (("VerifyStyles", "VerifyMode"), "طريقة التحقق", "Verification mode", "text"),
    ]},
    {"id": "network", "icon": "🌐", "ar": "الشبكة", "en": "Network", "fields": [
        ("IPAddress", "عنوان IP", "IP address", "text"),
        ("NetMask", "قناع الشبكة", "Subnet mask", "text"),
        ("GATEIPAddress", "البوابة", "Gateway", "text"),
        (("DNS", "DNSServer"), "DNS", "DNS", "text"),
        ("DHCP", "DHCP", "DHCP", "bool"),
        ("COMKey", "مفتاح الاتصال", "Comm key", "number"),
        ("DeviceID", "رقم الجهاز", "Device ID", "number"),
        (("TCPPort", "UDPPort"), "منفذ الاتصال المباشر", "Direct link port", "number"),
    ]},
    {"id": "server", "icon": "☁", "ar": "الخادم السحابي", "en": "Cloud server", "fields": [
        ("WebServerIP", "عنوان الخادم", "Server address", "text"),
        ("WebServerPort", "منفذ الخادم", "Server port", "number"),
        ("WebServerURLModel", "استخدام اسم نطاق", "Use a domain name", "bool"),
        ("ICLOCKSVRURL", "رابط الخادم", "Server URL", "text"),
        (("ProxyServerIP", "ProxyIP"), "خادم الوكيل", "Proxy server", "text"),
        (("ProxyServerPort", "ProxyPort"), "منفذ الوكيل", "Proxy port", "number"),
    ]},
    {"id": "personal", "icon": "🔊", "ar": "الصوت والشاشة", "en": "Sound & display", "fields": [
        ("VOLUME", "مستوى الصوت", "Volume", "number"),
        ("VoiceOn", "الإرشاد الصوتي", "Voice prompts", "bool"),
        (("KeyBeep", "KeyBeepOn"), "صوت اللمس", "Touch sound", "bool"),
        (("IdleMinute", "SleepTime"), "إطفاء الشاشة بعد (دقيقة)", "Screen off after (min)", "number"),
        (("Brightness", "LcdBrightness"), "سطوع الشاشة", "Brightness", "number"),
        ("Language", "اللغة", "Language", "text"),
    ]},
    {"id": "camera", "icon": "📷", "ar": "صور الحضور", "en": "Punch photos", "fields": [
        ("CameraOpen", "التقاط صورة عند البصمة", "Photo at each punch", "bool"),
        ("CapturePic", "حفظ صور الحضور", "Keep punch photos", "bool"),
        ("PhotoFunOn", "دعم الصور", "Photo support", "ro"),
    ]},
    {"id": "access", "icon": "🔒", "ar": "القفل والتحقق", "en": "Lock & verification", "fields": [
        ("LockOn", "مدة فتح القفل (ثانية)", "Unlock time (s)", "number"),
        ("FaceFunOn", "التعرف على الوجه", "Face recognition", "ro"),
        ("FingerFunOn", "البصمة", "Fingerprint", "ro"),
        ("PvFunOn", "الكف", "Palm", "ro"),
    ]},
]
DANGEROUS = {"IPAddress", "NetMask", "GATEIPAddress", "DHCP", "COMKey", "WebServerIP", "WebServerPort",
             "ICLOCKSVRURL", "WebServerURLModel", "DNS", "DNSServer", "TCPPort", "UDPPort", "ProxyServerIP",
             "ProxyIP", "ProxyServerPort", "ProxyPort", "DeviceID"}


def _names(key) -> tuple[str, ...]:
    return key if isinstance(key, tuple) else (key,)


KNOWN = {n for s in SECTIONS for f in s["fields"] for n in _names(f[0])}
# answered from the counters / clock, never asked for as options
COMPUTED = {"UserCount", "FPCount", "FaceCount", "PvCount", "TransactionCount", "MaxUserCount", "MaxFingerCount",
            "MaxFaceCount", "MaxAttLogCount", "DeviceTime", "FWVersion"}
# option names a terminal did not answer; not asked again until the program restarts
_UNANSWERED: dict[str, set[str]] = {}
LIVE_BUDGET = 12.0   # seconds a live read may take before the panel shows what it has


class ConsoleError(Exception):
    pass


def _device(sn: str) -> dict:
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        if d is None:
            raise ConsoleError("device not found")
        try:
            opts = json.loads(d.options or "{}")
        except json.JSONDecodeError:
            opts = {}
        return {"ip": d.ip, "port": d.tcp_port or 4370, "key": d.comm_key or "0", "direct": d.managed_by == "tcp",
                "opts": {k.lstrip("~"): v for k, v in opts.items()}}


def _lock(sn: str, wait: float):
    from . import tcp_pull as T
    lk = T._lock(sn)
    if not lk.acquire(timeout=wait):
        return None
    return lk


def read(sn: str, live: bool = True, budget: float = LIVE_BUDGET) -> dict:
    """Current values: live from the terminal when it answers on 4370 (within ``budget``
    seconds), else as it last reported them. Never waits long: when a background read is
    using the terminal the panel shows the saved values and says so."""
    import time as _t
    from . import tcp_pull as T
    from .terminal.client import SERVER_OPTIONS, TerminalError
    dev = _device(sn)
    values, got_live, busy = dict(dev["opts"]), False, False
    if live and dev["ip"]:
        lk = _lock(sn, 2.5)
        if lk is None:
            busy = True
        else:
            end = _t.monotonic() + budget
            try:
                term = T._client(dev["ip"], dev["port"], dev["key"], 4)
                try:
                    try:
                        values["DeviceTime"] = term.get_time().strftime("%Y-%m-%d %H:%M:%S")
                        s_ = term.read_sizes()
                        values.update(UserCount=str(s_.users), FPCount=str(s_.fingers), TransactionCount=str(s_.records),
                                      FaceCount=str(s_.faces), MaxUserCount=str(s_.users_cap),
                                      MaxFingerCount=str(s_.fingers_cap), MaxAttLogCount=str(s_.records_cap),
                                      MaxFaceCount=str(s_.faces_cap))
                        fw = term.get_firmware()
                        if fw:
                            values["FWVersion"] = fw
                    except TerminalError:
                        pass
                    skip = _UNANSWERED.setdefault(sn, set())
                    keys = [k for k in (*KNOWN, *SERVER_OPTIONS, "~SerialNumber", "~DeviceName", "~Platform",
                                        "~ZKFPVersion", "~ProductTime") if k not in COMPUTED and k not in skip]
                    fails = 0
                    for key in keys:
                        if _t.monotonic() > end:
                            break
                        try:
                            v = term.get_option(key)
                            fails = 0
                        except TerminalError:
                            fails += 1
                            if fails >= 2:      # the link stalled: keep what we have
                                break
                            continue
                        if v != "":
                            values[key.lstrip("~")] = v
                        else:
                            skip.add(key)
                    got_live = True
                finally:
                    term.disconnect()
            except (T.TcpPullError, OSError) as exc:
                log.info("panel of %s: no live link (%s)", sn, exc)
            finally:
                lk.release()
    sections = []
    for s in SECTIONS:
        fields = []
        for key, ar, en, kind in s["fields"]:
            name = next((n.lstrip("~") for n in _names(key) if values.get(n.lstrip("~")) not in (None, "")), None)
            if name is None:
                continue
            fields.append({"key": name, "ar": ar, "en": en, "kind": kind, "value": values.get(name),
                           "danger": name in DANGEROUS})
        sections.append({k: s[k] for k in ("id", "icon", "ar", "en")} | {"fields": fields})
    known = {n.lstrip("~") for n in KNOWN} | COMPUTED
    advanced = {k: v for k, v in sorted(values.items()) if k not in known and not isinstance(v, (dict, list))}
    return {"live": got_live, "busy": busy, "direct": dev["direct"], "has_ip": bool(dev["ip"]),
            "sections": sections, "advanced": advanced}


def read_option(sn: str, key: str) -> str | None:
    """One option by name, live (for the "any setting" box). None when there is no live link."""
    from . import tcp_pull as T
    from .terminal.client import TerminalError
    dev = _device(sn)
    if not dev["ip"]:
        return None
    lk = _lock(sn, 5)
    if lk is None:
        raise ConsoleError("busy")
    try:
        term = T._client(dev["ip"], dev["port"], dev["key"], 5)
        try:
            return term.get_option(key)
        finally:
            term.disconnect()
    except (T.TcpPullError, OSError, TerminalError):
        return None
    finally:
        lk.release()


def set_time(sn: str, when) -> bool:
    """Set the terminal clock live. False when it cannot be reached on 4370."""
    from . import tcp_pull as T
    from .terminal.client import TerminalError
    dev = _device(sn)
    if not dev["ip"]:
        return False
    lk = _lock(sn, 10)
    if lk is None:
        raise ConsoleError("busy")
    try:
        term = T._client(dev["ip"], dev["port"], dev["key"], 6)
        try:
            term.set_time(when)
            return True
        finally:
            term.disconnect()
    except (T.TcpPullError, OSError, TerminalError):
        return False
    finally:
        lk.release()


def write(sn: str, changes: dict[str, str]) -> dict:
    """Write options: live over 4370 when the terminal answers, else queued for its next
    connection (port 90). Returns {"applied": {...read back...}} or {"queued": n}."""
    from .adms import commands as C
    from .adms import sync
    if not changes:
        return {"applied": {}}
    clean = {str(k).strip(): str(v).strip() for k, v in changes.items()
             if str(k).strip() and "\n" not in str(v) and "=" not in str(k)}
    dev = _device(sn)
    if dev["ip"]:
        back = _write_live(sn, dev, clean)
        if back is not None:
            _remember(sn, clean)
            return {"applied": back}
        if dev["direct"]:
            raise ConsoleError("the terminal does not answer on port 4370")
    with session_scope() as db:
        n = 0
        for k, v in clean.items():
            if sync.queue(db, sn, f"SET OPTION {k}={v}", f"Set {k}"):
                n += 1
        sync.queue(db, sn, C.SIMPLE["reload_options"], "Reload options")
    _remember(sn, clean)
    return {"queued": n}


def _write_live(sn: str, dev: dict, clean: dict[str, str]) -> dict | None:
    """Write over 4370 and read the values back; None when the terminal cannot be reached."""
    from . import tcp_pull as T
    from .terminal import protocol as P
    from .terminal.client import TerminalError
    lk = _lock(sn, 15)
    if lk is None:
        if dev["direct"]:
            raise ConsoleError("الجهاز مشغول بقراءة البيانات الآن، حاول بعد دقيقة. / "
                               "The terminal is busy being read, try again in a minute.")
        return None   # queue it for the terminal's next push connection instead
    try:
        term = T._client(dev["ip"], dev["port"], dev["key"], 8)
        try:
            for k, v in clean.items():
                term.set_option(k, v)
            term.command(P.CMD_REFRESHOPTION)
            back = {}
            for k in clean:
                try:
                    back[k] = term.get_option(k)
                except TerminalError:
                    back[k] = None
            return back
        finally:
            term.disconnect()
    except (T.TcpPullError, OSError):
        return None
    finally:
        lk.release()


def _remember(sn: str, clean: dict[str, str]) -> None:
    """Keep what the program relies on in step with the terminal (comm key, address)."""
    from .adms import sync
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == sn))
        if "COMKey" in clean:
            d.comm_key = clean["COMKey"]
        if "IPAddress" in clean:
            d.ip = clean["IPAddress"]
        sync.apply_device_info(d, clean)
