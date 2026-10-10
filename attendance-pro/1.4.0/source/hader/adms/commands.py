"""Builders for the ``C:<id>:<command>`` strings queued for a terminal."""
from __future__ import annotations

from datetime import datetime

from .protocol import tsv, dev_encode_time


def user_update(emp) -> str:
    name = (emp.full_name or emp.emp_code)[:24]
    return "DATA UPDATE USERINFO " + tsv(
        PIN=emp.emp_code, Name=name, Pri=emp.dev_privilege or 0, Passwd=emp.dev_password or "",
        Card=emp.card_no or "", Grp=1, TZ="0000000100000000",
        Verify=emp.verify_mode if emp.verify_mode is not None else -1, ViceCard="",
        StartDatetime=0, EndDatetime=0)


def user_delete(pin: str) -> str:
    return "DATA DELETE USERINFO " + tsv(PIN=pin)


def biodata_update(pin: str, t) -> str:
    return "DATA UPDATE BIODATA " + tsv(
        Pin=pin, No=t.bio_no, Index=t.bio_index, Valid=t.valid, Duress=t.duress, Type=t.bio_type,
        MajorVer=t.major_ver or 0, MinorVer=t.minor_ver or 0, Format=t.bio_format, Tmp=t.template)


def fingertmp_update(pin: str, t) -> str:
    """Legacy (pre-BIODATA) fingerprint command, fingerprint algorithm 10."""
    return "DATA UPDATE FINGERTMP " + tsv(PIN=pin, FID=t.bio_no, Size=len(t.template),
                                          Valid=t.valid, TMP=t.template)


def face_update(pin: str, t) -> str:
    """Legacy near-infrared face (face algorithm 7)."""
    return "DATA UPDATE FACE " + tsv(PIN=pin, FID=t.bio_no, Valid=t.valid, Size=len(t.template),
                                     TMP=t.template)


def fingertmp_delete(pin: str, fid: int | None = None) -> str:
    return "DATA DELETE FINGERTMP " + (tsv(PIN=pin) if fid is None else tsv(PIN=pin, FID=fid))


def face_delete(pin: str) -> str:
    return "DATA DELETE FACE " + tsv(PIN=pin)


def biodata_delete(pin: str, bio_type: int | None = None, no: int | None = None,
                   index: int | None = None) -> str:
    values = {"Pin": pin}
    if bio_type is not None:
        values["Type"] = bio_type
    if no is not None:
        values["No"] = no
    if index is not None:
        values["Index"] = index
    return "DATA DELETE BIODATA " + tsv(**values)


def userpic_update(pin: str, b64: str) -> str:
    return "DATA UPDATE USERPIC " + tsv(PIN=pin, Size=len(b64), Content=b64)


def biophoto_update(pin: str, bio_type: int, b64: str) -> str:
    return "DATA UPDATE BIOPHOTO " + tsv(PIN=pin, Type=bio_type, Size=len(b64), Content=b64,
                                         Format=0, Url="", PostBackTmpFlag=1)


def query_attlog(start: datetime, end: datetime) -> str:
    return "DATA QUERY ATTLOG " + tsv(StartTime=start.strftime("%Y-%m-%d %H:%M:%S"),
                                      EndTime=end.strftime("%Y-%m-%d %H:%M:%S"))


def query_table(table: str, pin: str | None = None) -> str:
    """Ask the terminal to upload a table (all rows, or one person's)."""
    return f"DATA QUERY tablename={table},fielddesc=*,filter={'Pin=' + pin if pin else '*'}"


def set_time(dt: datetime) -> str:
    return f"SET OPTION DateTime={dev_encode_time(dt)}"


def enroll_bio(pin: str, bio_type: int, finger: int = 0, legacy: bool = True) -> str:
    """Remote enrollment: the terminal asks the employee for a finger / face / palm and
    uploads the template by itself. ``legacy`` = firmware without BIODATA (ENROLL_FP)."""
    if bio_type == 1 and legacy:
        return "ENROLL_FP " + tsv(PIN=pin, FID=finger, RETRY=3, OVERWRITE=1)
    if bio_type == 1:
        return "ENROLL_BIO " + tsv(TYPE=1, PIN=pin, NO=finger, FID=finger, CardNo="", RETRY=3, OVERWRITE=1)
    return "ENROLL_BIO " + tsv(TYPE=bio_type, PIN=pin, CardNo="", RETRY=3, OVERWRITE=1)


SIMPLE = {
    "check": "CHECK",          # device re-reads the option block
    "info": "INFO",            # device reports firmware / counters
    "reboot": "REBOOT",
    "clear_log": "CLEAR LOG",  # attendance records
    "clear_photo": "CLEAR PHOTO",
    "clear_data": "CLEAR DATA",  # users, templates and records
    "reload_options": "RELOAD OPTIONS",
}


def normalized(content: str) -> str:
    return " ".join(content.upper().split())


def changes_device(content: str) -> bool:
    """Only documented read/query commands are allowed without write permission."""
    text = normalized(content)
    return not (text == "INFO" or text.startswith("DATA QUERY "))


def can_retry(content: str) -> bool:
    """A lost reply is not evidence that a destructive operation did not execute."""
    text = normalized(content)
    return (text in ("INFO", "CHECK", "RELOAD OPTIONS") or
            text.startswith(("DATA QUERY ", "DATA UPDATE ", "SET OPTION ")))


def clears_data(content: str) -> bool:
    return normalized(content).startswith("CLEAR ")
