from datetime import datetime, timezone

import pytest

from hader import timekeeping as T
from hader.config import settings


@pytest.fixture
def clock_area(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.delenv("HADER_TIMEZONE", raising=False)
    return tmp_path


def test_zone_default_preserves_legacy_wall_time(clock_area):
    assert T.zone_name() == ""
    assert T.wall_time_info(datetime(2026, 3, 8, 2, 30))["kind"] == "unspecified"
    assert T.duration_minutes(datetime(2026, 3, 8, 1), datetime(2026, 3, 8, 4)) == 180


def test_persisted_zone_and_invalid_zone_do_not_replace_existing(clock_area):
    assert T.configure_zone("Asia/Hebron") == "Asia/Hebron"
    before = (clock_area / "timezone.ini").read_bytes()
    with pytest.raises(ValueError):
        T.configure_zone("invalid/unknown")
    assert (clock_area / "timezone.ini").read_bytes() == before
    assert T.zone_name() == "Asia/Hebron"
    assert not list(clock_area.glob("*.tmp"))


def test_child_environment_overrides_saved_zone(clock_area, monkeypatch):
    T.configure_zone("Asia/Hebron")
    monkeypatch.setenv("HADER_TIMEZONE", "UTC")
    assert T.zone_name() == "UTC"
    value = T.now_local()
    assert value.tzinfo is None and value.microsecond == 0
    assert abs((value - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()) < 2


@pytest.mark.parametrize("stamp,kind,candidates", [
    (datetime(2026, 3, 8, 1, 30), "normal", ["2026-03-08T06:30:00Z"]),
    (datetime(2026, 3, 8, 2, 30), "nonexistent", []),
    (datetime(2026, 11, 1, 1, 30), "ambiguous", ["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"]),
])
def test_dst_gap_and_repeated_hour_are_explicit(clock_area, stamp, kind, candidates):
    T.configure_zone("America/New_York")
    info = T.wall_time_info(stamp)
    assert info == {"kind": kind, "zone": "America/New_York", "utc_candidates": candidates}


def test_elapsed_spring_and_fall_spans_differ_from_wall_duration(clock_area):
    T.configure_zone("America/New_York")
    spring = (datetime(2026, 3, 8, 0), datetime(2026, 3, 8, 4))
    fall = (datetime(2026, 11, 1, 0), datetime(2026, 11, 1, 4))
    assert T.duration_minutes(*spring) == 240
    assert T.duration_minutes(*spring, mode="elapsed") == 180
    assert T.duration_minutes(*fall, mode="elapsed") == 300


@pytest.mark.parametrize("stamp", [datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)])
def test_elapsed_refuses_to_guess_invalid_or_ambiguous_punch(clock_area, stamp):
    T.configure_zone("America/New_York")
    with pytest.raises(ValueError, match="Elapsed"):
        T.duration_minutes(stamp, datetime(2026, 11, 2), mode="elapsed")


def test_elapsed_needs_zone_or_explicit_offsets(clock_area):
    with pytest.raises(ValueError):
        T.duration_minutes(datetime(2026, 1, 1), datetime(2026, 1, 2), mode="elapsed")
    first = datetime.fromisoformat("2026-11-01T01:30:00-04:00")
    second = datetime.fromisoformat("2026-11-01T01:30:00-05:00")
    assert T.duration_minutes(first, second, mode="elapsed") == 60
    assert T.duration_minutes(first, second) == 0


def test_db_now_uses_institutional_zone(clock_area, monkeypatch):
    from hader.db import now
    T.configure_zone("Pacific/Honolulu")
    from zoneinfo import ZoneInfo
    expected = datetime.now(ZoneInfo("Pacific/Honolulu")).replace(tzinfo=None)
    assert abs((now() - expected).total_seconds()) < 2
