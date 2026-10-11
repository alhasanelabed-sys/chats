"""Institutional wall clocks, with explicit DST ambiguity detection.

Device timestamps remain naive local wall times.  Historical stamps are never
rewritten when an administrator selects a zone.  Elapsed-time calculations need
an unambiguous UTC interpretation; they do not infer a DST fold from punch order.
"""
from __future__ import annotations

import configparser
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_OVERRIDES: dict[str, str] = {}
_CACHE: dict[tuple[str, str, int], str] = {}


def validate_zone(name: str) -> str:
    if not isinstance(name, str) or len(name) > 128:
        raise ValueError("منطقة زمنية غير صالحة / Invalid time zone")
    name = name.strip()
    if name:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("استخدم اسم منطقة IANA مثل Asia/Hebron / Use an IANA time zone") from exc
    return name


def _path() -> Path:
    from .config import settings
    return settings.data_dir / "timezone.ini"


def configure_zone(name: str, persist: bool = True) -> str:
    name = validate_zone(name)
    path = _path()
    if persist:
        path.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        cp["time"] = {"zone": name}
        fd, temporary = tempfile.mkstemp(prefix=".timezone_", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                cp.write(stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        _OVERRIDES.pop(str(path), None)
    else:
        _OVERRIDES[str(path)] = name
    _CACHE.clear()
    return name


def zone_name() -> str:
    override = os.getenv("HADER_TIMEZONE")
    if override is not None:
        return validate_zone(override)
    path = _path()
    if str(path) in _OVERRIDES:
        return _OVERRIDES[str(path)]
    try:
        stat = path.stat()
    except FileNotFoundError:
        return ""
    key = (str(path), str(stat.st_size), stat.st_mtime_ns)
    if key not in _CACHE:
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(path, encoding="utf-8")
        _CACHE.clear()
        _CACHE[key] = validate_zone(cp.get("time", "zone", fallback=""))
    return _CACHE[key]


def now_local() -> datetime:
    name = zone_name()
    stamp = datetime.now(ZoneInfo(name)) if name else datetime.now()
    return stamp.replace(tzinfo=None, microsecond=0)


def wall_time_info(stamp: datetime) -> dict:
    if not isinstance(stamp, datetime) or stamp.tzinfo is not None:
        raise ValueError("wall time must be a naive datetime")
    name = zone_name()
    if not name:
        return {"kind": "unspecified", "zone": "", "utc_candidates": []}
    zone = ZoneInfo(name)
    candidates = set()
    for fold in (0, 1):
        instant = stamp.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        # A nonexistent local stamp does not survive a round trip through UTC.
        if instant.astimezone(zone).replace(tzinfo=None) == stamp:
            candidates.add(instant)
    values = sorted(candidates)
    return {"kind": "nonexistent" if not values else "ambiguous" if len(values) > 1 else "normal",
            "zone": name,
            "utc_candidates": [v.isoformat().replace("+00:00", "Z") for v in values]}


def duration_minutes(start: datetime, end: datetime, mode: str = "wall") -> float:
    if mode not in {"wall", "elapsed"}:
        raise ValueError("duration mode must be wall or elapsed")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("duration endpoints must be datetimes")
    if (start.tzinfo is None) != (end.tzinfo is None):
        raise ValueError("duration endpoints must use the same time representation")
    if mode == "wall":
        return (end.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds() / 60
    if start.tzinfo is not None:
        return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 60
    endpoints = [wall_time_info(stamp) for stamp in (start, end)]
    if any(info["kind"] != "normal" for info in endpoints):
        raise ValueError("مدة فعلية غير مؤكدة بسبب المنطقة الزمنية أو انتقال الساعة / Elapsed duration needs unambiguous timestamps")
    instants = [datetime.fromisoformat(info["utc_candidates"][0].replace("Z", "+00:00")) for info in endpoints]
    return (instants[1] - instants[0]).total_seconds() / 60
