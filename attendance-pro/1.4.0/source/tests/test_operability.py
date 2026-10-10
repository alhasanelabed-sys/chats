import hashlib
import json
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from hader import backup as B, operability as O
from hader.config import settings


@pytest.fixture
def backup_area(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "photos").mkdir()
    (data / "backups").mkdir()
    database = data / "hader.db"
    with sqlite3.connect(database) as db:
        db.executescript('''
            CREATE TABLE employee(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO employee VALUES(1, 'one'), (2, 'two');
            CREATE TABLE audit_log(id INTEGER PRIMARY KEY, action TEXT);
            INSERT INTO audit_log VALUES(1, 'created');
            CREATE TABLE device(id INTEGER PRIMARY KEY);
            INSERT INTO device VALUES(1);
            CREATE TABLE "transaction"(id INTEGER PRIMARY KEY);
            INSERT INTO "transaction" VALUES(1), (2);
        ''')
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + database.as_posix())
    monkeypatch.delenv("HADER_STORAGE_SYNCHRONOUS", raising=False)
    marker = data / ".secret_key"
    marker.write_text("private-live-secret", encoding="utf-8")
    return data


def finish(manager):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = manager.status()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("real backup worker did not finish")


def test_durability_applies_to_new_engine_with_restart_semantics(backup_area):
    from hader.db import _make_engine
    O.configure_synchronous("NORMAL")
    first = _make_engine("sqlite:///" + (backup_area / "normal.db").as_posix())
    second = None
    try:
        with first.connect() as db:
            assert db.exec_driver_sql("PRAGMA synchronous").scalar() == 1
            assert db.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        O.configure_synchronous("FULL")
        with first.connect() as db:
            assert db.exec_driver_sql("PRAGMA synchronous").scalar() == 1
        second = _make_engine("sqlite:///" + (backup_area / "full.db").as_posix())
        with second.connect() as db:
            assert db.exec_driver_sql("PRAGMA synchronous").scalar() == 2
        with pytest.raises(ValueError):
            O.configure_synchronous("OFF")
        assert O.synchronous_mode() == "FULL"
    finally:
        first.dispose()
        if second is not None:
            second.dispose()


def test_external_folder_is_optional_absolute_and_outside_data(backup_area):
    assert O.validate_offsite_path("") == ""
    for value in ("relative", str(backup_area), str(backup_area / "copies"), "bad\npath"):
        with pytest.raises(ValueError):
            O.validate_offsite_path(value)
    assert O.validate_offsite_path(str(backup_area.parent / "external")) == str(backup_area.parent / "external")


def test_real_archive_external_copy_and_rehearsal_preserve_live_data(backup_area):
    original = (backup_area / "hader.db").read_bytes()
    archive = B.create_snapshot("verified")
    offsite = backup_area.parent / "external"
    manager = O.BackupCheckManager(backup_area)
    try:
        assert manager.start(archive, str(offsite))["status"] == "running"
        state = finish(manager)
        assert state["status"] == "passed", state
        assert state["counts"] == {"employee": 2, "device": 1, "transaction": 2, "audit_log": 1}
        assert state["manifest_verified"] is True
        assert state["offsite_copy_verified"] is True
        assert state["restore_apply_performed"] is False
        assert state["integrity"] == "ok"
        assert hashlib.sha256(archive.read_bytes()).digest() == hashlib.sha256((offsite / archive.name).read_bytes()).digest()
        assert (backup_area / "hader.db").read_bytes() == original
        assert (backup_area / ".secret_key").read_text() == "private-live-secret"
        assert not (backup_area / ".pending_restore.json").exists()
        assert not list(manager.root.iterdir())
        assert not list(offsite.glob(".*.tmp*"))
        assert str(offsite) not in json.dumps(state)
    finally:
        manager.shutdown()


def test_archive_hash_fault_fails_without_exporting_bad_copy(backup_area):
    archive = B.create_snapshot("fault")
    with zipfile.ZipFile(archive) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    contents["hader.db"] = contents["hader.db"] + b"tampering"
    with zipfile.ZipFile(archive, "w") as altered:
        for name, value in contents.items():
            altered.writestr(name, value)
    manager = O.BackupCheckManager(backup_area)
    offsite = backup_area.parent / "external"
    try:
        manager.start(archive, str(offsite))
        assert finish(manager)["status"] == "failed"
        assert not (offsite / archive.name).exists()
        assert not list(manager.root.iterdir())
    finally:
        manager.shutdown()


def test_legacy_database_can_be_rehearsed_and_copied(backup_area):
    manager = O.BackupCheckManager(backup_area)
    offsite = backup_area.parent / "external"
    try:
        manager.start(backup_area / "hader.db", str(offsite))
        state = finish(manager)
        assert state["status"] == "passed", state
        assert state["counts"]["employee"] == 2
        assert state["manifest_verified"] is False
        assert state["offsite_copy_verified"] is True
    finally:
        manager.shutdown()


def test_timeout_automatically_stops_worker_and_cleans_private_data(backup_area):
    archive = B.create_snapshot("timeout")
    manager = O.BackupCheckManager(backup_area)
    manager.TIMEOUT_SECONDS = 0.001
    try:
        manager.start(archive)
        child = manager.process
        child.wait(timeout=10)
        state = finish(manager)
        assert state["status"] == "failed"
        assert "timed out" in state["error"]
        assert not list(manager.root.iterdir())
        assert archive.exists()
    finally:
        manager.shutdown()


def test_no_external_permission_does_not_discard_local_snapshot(backup_area):
    archive = B.create_snapshot("destination-fault")
    # A file used as the parent directory creates a portable real I/O failure.
    blocked = backup_area.parent / "blocked"
    blocked.write_text("a file")
    manager = O.BackupCheckManager(backup_area)
    try:
        manager.start(archive, str(blocked / "external"))
        state = finish(manager)
        assert state["status"] == "failed"
        assert archive.exists()
        B.validate_snapshot(archive)
        assert not list(manager.root.iterdir())
        assert str(blocked) not in json.dumps(state)
    finally:
        manager.shutdown()


def test_unknown_clock_and_read_only_clock_check(client, monkeypatch, tmp_path):
    from hader import models as m, store
    from hader.db import now, session_scope
    from datetime import timedelta
    with session_scope() as db:
        device = m.Device(sn="CLOCK-TEST", alias="Clock", ip="192.0.2.1", enabled=True)
        db.add(device)
        db.flush()
        device_id = device.id
        store.set_(db, "tcp.write_back", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert O.clock_status("CLOCK-TEST") == {"status": "unknown", "measured": False}
    calls = []
    def read(ip, port, key):
        calls.append((ip, port))
        return now() + timedelta(seconds=120)
    monkeypatch.setattr(O, "read_clock", read)
    response = client.post(f"/api/operations/devices/{device_id}/clock-check")
    assert response.status_code == 200, response.text
    assert calls == [("192.0.2.1", 4370)]
    result = response.json()
    assert result["status"] == "drift" and result["drift_seconds"] >= 119
    assert result["clock_modified"] is False
    with session_scope() as db:
        assert store.get(db, "tcp.write_back") is False
        assert db.query(m.DeviceCommand).count() == 0


def test_health_has_no_credential_or_filesystem_paths(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    response = client.get("/api/operations/health")
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["storage"]["journal_mode"] == "wal"
    assert state["storage"]["power_loss_tested"] is False
    assert state["backup"]["restore_apply_performed"] is False
    assert state["disk"]["free_bytes"] > 0
    assert str(tmp_path) not in response.text


def test_operations_require_authentication(client):
    client.post("/api/auth/logout")
    assert client.get("/api/operations/health").status_code == 401
    assert client.post("/api/operations/backup-check").status_code == 401
    assert client.post("/api/operations/devices/1/clock-check").status_code == 401
