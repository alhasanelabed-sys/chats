"""Recovery rehearsals run only against isolated temporary databases."""
import json
import os
import sqlite3
import stat
import subprocess
import sys
import zipfile
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hader import backup as B, models as m
from hader.config import Settings
from hader.db import Base


@pytest.fixture()
def recovery_data(tmp_path, monkeypatch):
    data = tmp_path / "isolated-data"
    data.mkdir()
    s = Settings(data, f"sqlite:///{data / 'hader.db'}", "127.0.0.1", 8090,
                 secret_key="a" * 64)
    s.photos_dir.mkdir()
    (data / ".secret_key").write_text(s.secret_key)
    (data / "network.ini").write_text("[server]\nhost=127.0.0.1\nweb_port=8090\n")
    (s.photos_dir / "001.jpg").write_bytes(b"photo-one")
    eng = create_engine(s.database_url)
    Base.metadata.create_all(eng)
    with Session(eng) as db:
        db.add(m.Employee(emp_code="001", first_name="Original"))
        db.commit()
    eng.dispose()
    monkeypatch.setattr(B, "settings", s)
    return s


def _name(s):
    with sqlite3.connect(s.database_url.split("///", 1)[1]) as db:
        return db.execute('SELECT first_name FROM employee WHERE emp_code="001"').fetchone()[0]


def _change(s, name):
    with sqlite3.connect(s.database_url.split("///", 1)[1]) as db:
        db.execute("UPDATE employee SET first_name=? WHERE emp_code='001'", (name,))


def test_snapshot_captures_wal_photos_configuration_and_secret_only(recovery_data):
    s = recovery_data
    (s.data_dir / "logs").mkdir()
    (s.data_dir / "logs" / "secret.log").write_text("excluded")
    (s.data_dir / "initial_admin_password.txt").write_text("excluded")
    with sqlite3.connect(s.data_dir / "hader.db") as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("UPDATE employee SET first_name='Committed WAL'")
        source.commit()
        path = B.create_snapshot("manual")
    meta = B.validate_snapshot(path)
    assert meta["format"] == "snapshot_zip" and meta["schema_version"] == 1
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == {"manifest.json", "hader.db", "photos/001.jpg", "network.ini", ".secret_key"}
        manifest = json.loads(z.read("manifest.json"))
        assert len(manifest["files"]["hader.db"]["sha256"]) == 64
        restored = s.data_dir / "inspect.db"
        restored.write_bytes(z.read("hader.db"))
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT first_name FROM employee").fetchone()[0] == "Committed WAL"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_schedule_does_not_touch_live_database_and_offline_apply_restores_all(recovery_data):
    s = recovery_data
    snapshot = B.create_snapshot()
    _change(s, "Changed")
    (s.photos_dir / "001.jpg").write_bytes(b"changed-photo")
    (s.photos_dir / "extra.jpg").write_bytes(b"extra")
    (s.data_dir / ".secret_key").write_text("b" * 64)
    result = B.schedule_restore(snapshot, "manager\nforged-entry")
    assert result["scheduled"] and result["restart_required"] and _name(s) == "Changed"
    snapshot.unlink()  # pruning the source cannot invalidate a scheduled restore
    result = B.apply_pending_restore()
    assert result["ok"] and _name(s) == "Original"
    assert (s.photos_dir / "001.jpg").read_bytes() == b"photo-one"
    assert not (s.photos_dir / "extra.jpg").exists()
    assert (s.data_dir / ".secret_key").read_text() == "a" * 64
    assert B.apply_pending_restore() is None
    assert len(list(s.backups_dir.glob("*before_restore*.zip"))) == 1
    with sqlite3.connect(s.data_dir / "hader.db") as db:
        actor, action = db.execute("SELECT username,action FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        assert action == "restore" and "\n" not in actor
    logs = [json.loads(line) for line in (s.data_dir / "restore.log").read_text().splitlines()]
    assert logs[-1]["event"] == "restore.completed"


def test_legacy_database_restore_preserves_photos_and_configuration(recovery_data):
    s = recovery_data
    legacy = s.data_dir / "legacy.db"
    with sqlite3.connect(s.data_dir / "hader.db") as source, sqlite3.connect(legacy) as dest:
        source.backup(dest)
    _change(s, "Changed")
    (s.photos_dir / "001.jpg").write_bytes(b"current-photo")
    (s.photos_dir / "extra.jpg").write_bytes(b"current-extra")
    (s.data_dir / "network.ini").write_text("current network settings")
    (s.data_dir / ".secret_key").write_text("b" * 64)
    assert B.validate_snapshot(legacy)["format"] == "legacy_db"
    B.schedule_restore(legacy, "admin")
    assert B.apply_pending_restore()["ok"]
    assert _name(s) == "Original" and (s.photos_dir / "001.jpg").read_bytes() == b"current-photo"
    assert (s.photos_dir / "extra.jpg").read_bytes() == b"current-extra"
    assert (s.data_dir / "network.ini").read_text() == "current network settings"
    assert (s.data_dir / ".secret_key").read_text() == "b" * 64


@pytest.mark.parametrize("name", ["../outside", "/absolute", "photos/../../outside", "photos\\evil", "C:/evil"])
def test_rejects_unsafe_archive_members(recovery_data, name):
    path = recovery_data.data_dir / "hostile.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, "bad")
    with pytest.raises(B.BackupError):
        B.validate_snapshot(path)


def test_rejects_corrupt_hash_future_version_and_wrong_schema(recovery_data):
    s = recovery_data
    path = B.create_snapshot()
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files["photos/001.jpg"] = b"tampered"
    broken = s.data_dir / "broken.zip"
    with zipfile.ZipFile(broken, "w") as z:
        for n, content in files.items():
            z.writestr(n, content)
    with pytest.raises(B.BackupError):
        B.schedule_restore(broken, "admin")
    assert _name(s) == "Original" and not (s.data_dir / ".pending_restore.json").exists()
    manifest = json.loads(files["manifest.json"])
    manifest["app_version"] = "999.0.0"
    files["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(broken, "w") as z:
        for n, content in files.items():
            z.writestr(n, content)
    with pytest.raises(B.BackupError):
        B.validate_snapshot(broken)
    unrelated = s.data_dir / "unrelated.db"
    with sqlite3.connect(unrelated) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(B.BackupError):
        B.validate_snapshot(unrelated)


def test_partial_rollout_failure_returns_previous_data_and_files(recovery_data, monkeypatch):
    s = recovery_data
    snapshot = B.create_snapshot()
    _change(s, "Must survive")
    (s.photos_dir / "001.jpg").write_bytes(b"must-survive")
    original = B._replace
    failed = False

    def fail_photo_install(source, target):
        nonlocal failed
        if not failed and source.name == "photos" and source.parent.name == "new":
            failed = True
            raise OSError("simulated disk failure")
        return original(source, target)

    monkeypatch.setattr(B, "_replace", fail_photo_install)
    B.schedule_restore(snapshot, "admin")
    result = B.apply_pending_restore()
    assert not result["ok"] and _name(s) == "Must survive"
    assert (s.photos_dir / "001.jpg").read_bytes() == b"must-survive"
    assert not (s.data_dir / ".restore_journal.json").exists()
    assert json.loads((s.data_dir / "restore.log").read_text().splitlines()[-1])["event"] == "restore.failed"


def test_scheduled_artifact_tampering_is_rejected_without_data_change(recovery_data):
    s = recovery_data
    B.schedule_restore(B.create_snapshot(), "admin")
    marker = json.loads((s.data_dir / ".pending_restore.json").read_text())
    (s.data_dir / marker["file"]).write_bytes(b"tampered")
    assert not B.apply_pending_restore()["ok"] and _name(s) == "Original"


def test_second_pending_restore_is_rejected(recovery_data):
    path = B.create_snapshot()
    B.schedule_restore(path, "admin")
    with pytest.raises(B.BackupError):
        B.schedule_restore(path, "other")


def test_snapshot_and_validation_stream_database_and_photos(recovery_data, monkeypatch):
    from pathlib import Path
    original = Path.read_bytes

    def read_small_file(path):
        if path.suffix == ".db" or "photos" in path.parts:
            raise AssertionError("database and photos must not be read into memory in full")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_small_file)
    path = B.create_snapshot()
    assert B.validate_snapshot(path)["format"] == "snapshot_zip"
    B.schedule_restore(path, "admin")


def test_archive_size_is_checked_before_decompressing_members(recovery_data, monkeypatch):
    path = B.create_snapshot()
    monkeypatch.setattr(B, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(B.BackupError, match="too large"):
        B.validate_snapshot(path)


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("backup_format", ["snapshot_zip", "legacy_db"])
def test_snapshot_restores_configured_database_path_only(recovery_data, tmp_path, monkeypatch, external, backup_format):
    s = recovery_data
    database = (tmp_path / "external" if external else s.data_dir) / "custom.sqlite"
    database.parent.mkdir(exist_ok=True)
    (s.data_dir / "hader.db").rename(database)
    s = replace(s, database_url=f"sqlite:///{database}")
    monkeypatch.setattr(B, "settings", s)
    if backup_format == "snapshot_zip":
        snapshot = B.create_snapshot()
    else:
        from contextlib import closing
        snapshot = s.data_dir / "legacy.db"
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
    _change(s, "Must be replaced")
    # An unrelated file at the default location must survive a custom-path restore.
    (s.data_dir / "hader.db").write_bytes(b"unrelated default-path file")
    B.schedule_restore(snapshot, "admin")
    result = B.apply_pending_restore()
    assert result["ok"], result
    assert _name(s) == "Original"
    assert (s.data_dir / "hader.db").read_bytes() == b"unrelated default-path file"


def test_failure_saving_second_original_restores_first_original(recovery_data, monkeypatch):
    s = recovery_data
    snapshot = B.create_snapshot()
    _change(s, "Must survive")
    (s.photos_dir / "001.jpg").write_bytes(b"must-survive")
    original = B._replace

    def fail_save_network(source, target):
        if source.name == "network.ini" and target.parent.name == "old":
            raise OSError("simulated failure saving an original file")
        return original(source, target)

    monkeypatch.setattr(B, "_replace", fail_save_network)
    # Earlier releases saved originals with shutil.move, outside the rollback guard.
    original_move = B.shutil.move

    def fail_move_network(source, target, *args, **kwargs):
        from pathlib import Path
        if Path(source).name == "network.ini" and Path(target).parent.name == "old":
            raise OSError("simulated failure saving an original file")
        return original_move(source, target, *args, **kwargs)

    monkeypatch.setattr(B.shutil, "move", fail_move_network)
    B.schedule_restore(snapshot, "admin")
    result = B.apply_pending_restore()
    assert not result["ok"]
    assert _name(s) == "Must survive"
    assert (s.photos_dir / "001.jpg").read_bytes() == b"must-survive"
    assert (s.data_dir / "network.ini").is_file()


def test_restore_renames_use_staging_on_each_targets_volume(recovery_data, tmp_path, monkeypatch):
    from pathlib import Path
    s = recovery_data
    database = tmp_path / "other-volume" / "custom.sqlite"
    database.parent.mkdir()
    (s.data_dir / "hader.db").rename(database)
    s = replace(s, database_url=f"sqlite:///{database}")
    monkeypatch.setattr(B, "settings", s)
    snapshot = B.create_snapshot()
    original = B._replace
    renamed = []

    def require_same_volume(source, target):
        source, target = Path(source), Path(target)
        if source.parent.name == "new":
            assert source.parent.parent.parent == target.parent
        elif target.parent.name == "old":
            assert target.parent.parent.parent == source.parent
        else:
            pytest.fail("restore rename does not use sibling staging")
        renamed.append((source, target))
        return original(source, target)

    monkeypatch.setattr(B, "_replace", require_same_volume)
    B.schedule_restore(snapshot, "admin")
    assert B.apply_pending_restore()["ok"]
    assert len(renamed) == 8
    assert _name(s) == "Original"


def test_failed_rollback_preserves_originals_and_next_start_recovers(recovery_data, monkeypatch):
    from pathlib import Path
    s = recovery_data
    snapshot = B.create_snapshot()
    _change(s, "Must survive")
    (s.photos_dir / "001.jpg").write_bytes(b"must-survive")
    original = B._replace

    def fail_install_and_database_rollback(source, target):
        if source.name == "photos" and source.parent.name == "new":
            raise OSError("simulated photo installation failure")
        if source.name == "hader.db" and source.parent.name == "old":
            raise OSError("simulated rollback failure")
        return original(source, target)

    monkeypatch.setattr(B, "_replace", fail_install_and_database_rollback)
    B.schedule_restore(snapshot, "admin")
    result = B.apply_pending_restore()
    assert not result["ok"] and result["recovery_required"]
    journal_path = s.data_dir / ".restore_journal.json"
    journal = json.loads(journal_path.read_text())
    entry = next(e for e in journal["entries"] if e["rel"] == "hader.db")
    original_database = Path(entry["stage"]) / "old" / "hader.db"
    with sqlite3.connect(original_database) as db:
        assert db.execute("SELECT first_name FROM employee").fetchone()[0] == "Must survive"
    assert (s.data_dir / ".pending_restore.json").exists()
    with pytest.raises(B.BackupError, match="pending"):
        B.schedule_restore(snapshot, "admin")
    monkeypatch.setattr(B, "_replace", original)
    result = B.apply_pending_restore()
    assert result["ok"] and result["recovered"] and result["rolled_back"]
    assert _name(s) == "Must survive"
    assert (s.photos_dir / "001.jpg").read_bytes() == b"must-survive"
    assert not journal_path.exists()
    assert not (s.data_dir / ".pending_restore.json").exists()
    assert list(s.data_dir.glob(".hader_restore_*")) == []


class SimulatedCrash(BaseException):
    """A sudden stop bypasses ordinary exception-based rollback."""


def test_interrupted_installation_rolls_back_before_next_start(recovery_data, monkeypatch):
    s = recovery_data
    snapshot = B.create_snapshot()
    _change(s, "Before interrupted restore")
    (s.photos_dir / "001.jpg").write_bytes(b"before-interrupted-restore")
    original = B._replace

    def interrupt_after_database_install(source, target):
        if source.name == "photos" and source.parent.name == "new":
            raise SimulatedCrash()
        return original(source, target)

    monkeypatch.setattr(B, "_replace", interrupt_after_database_install)
    B.schedule_restore(snapshot, "admin")
    with pytest.raises(SimulatedCrash):
        B.apply_pending_restore()
    assert _name(s) == "Original"
    journal_path = s.data_dir / ".restore_journal.json"
    assert journal_path.exists()
    # Older ZIP recovery journals did not carry a format discriminator.
    journal = json.loads(journal_path.read_text())
    journal.pop("format")
    journal_path.write_text(json.dumps(journal))
    # Configuration loading on a subsequent process can create placeholder files.
    s.photos_dir.mkdir(exist_ok=True)
    (s.photos_dir / "placeholder.jpg").write_bytes(b"placeholder")
    monkeypatch.setattr(B, "_replace", original)
    result = B.apply_pending_restore()
    assert result["ok"] and result["rolled_back"]
    assert _name(s) == "Before interrupted restore"
    assert (s.photos_dir / "001.jpg").read_bytes() == b"before-interrupted-restore"
    assert not (s.photos_dir / "placeholder.jpg").exists()
    assert not (s.data_dir / ".restore_journal.json").exists()


def test_interrupted_committed_restore_keeps_installed_snapshot(recovery_data, monkeypatch):
    s = recovery_data
    snapshot = B.create_snapshot()
    _change(s, "Before restore")
    original_cleanup = B._cleanup_restore

    def interrupt_cleanup(journal):
        raise SimulatedCrash()

    monkeypatch.setattr(B, "_cleanup_restore", interrupt_cleanup)
    B.schedule_restore(snapshot, "admin")
    with pytest.raises(SimulatedCrash):
        B.apply_pending_restore()
    assert _name(s) == "Original"
    assert json.loads((s.data_dir / ".restore_journal.json").read_text())["phase"] == "committed"
    monkeypatch.setattr(B, "_cleanup_restore", original_cleanup)
    result = B.apply_pending_restore()
    assert result["ok"] and result["recovered"] and not result["rolled_back"]
    assert _name(s) == "Original"
    assert not (s.data_dir / ".restore_journal.json").exists()
    assert list(s.data_dir.glob(".hader_restore_*")) == []


@pytest.mark.parametrize("original_exists", [True, False])
@pytest.mark.parametrize("backup_format", ["snapshot_zip", "legacy_db"])
def test_real_process_crash_audit_wal_cannot_overwrite_rolled_back_database(recovery_data, original_exists, backup_format):
    from contextlib import closing
    from pathlib import Path
    s = recovery_data
    insert = ("INSERT INTO audit_log(username,action,target,detail,ip,created_at) "
              "VALUES('before','before','employee','','',datetime('now'))")
    with closing(sqlite3.connect(s.data_dir / "hader.db")) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        db.execute(insert)
        db.commit()
    if backup_format == "snapshot_zip":
        snapshot = B.create_snapshot()
    else:
        snapshot = s.data_dir / "legacy.db"
        with closing(sqlite3.connect(s.data_dir / "hader.db")) as source, closing(sqlite3.connect(snapshot)) as dest:
            source.backup(dest)
    _change(s, "Must survive process crash")
    with closing(sqlite3.connect(s.data_dir / "hader.db")) as db:
        for _ in range(40):
            db.execute(insert)
        db.commit()
    if not original_exists:
        (s.data_dir / "hader.db").unlink()
        (s.data_dir / "hader.db-wal").write_bytes(b"orphan WAL from a missing database")
        (s.data_dir / "hader.db-shm").write_bytes(b"orphan shared memory")
    B.schedule_restore(snapshot, "admin")
    program = """
import os, sqlite3, sys
from pathlib import Path
from hader import backup as B
from hader.config import Settings
data = Path(sys.argv[1])
B.settings = Settings(data, f"sqlite:///{data / 'hader.db'}", '127.0.0.1', 8090, secret_key='a' * 64)
def crash_audit(actor, action='restore'):
    db = sqlite3.connect(B._db_path())
    assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    db.execute("INSERT INTO audit_log(username,action,target,detail,ip,created_at) VALUES('restore','restore','system','','',datetime('now'))")
    db.commit()
    os._exit(88)  # No connection close: the restored snapshot WAL stays on disk.
B._audit_restore = crash_audit
B.apply_pending_restore()
os._exit(99)
"""
    result = subprocess.run([sys.executable, "-c", program, str(s.data_dir)],
                            cwd=Path(__file__).resolve().parent.parent, timeout=15, capture_output=True)
    assert result.returncode == 88, result.stderr.decode()
    assert (s.data_dir / "hader.db-wal").exists()
    result = B.apply_pending_restore()
    assert result["ok"] and result["rolled_back"]
    if original_exists:
        assert _name(s) == "Must survive process crash"
        with closing(sqlite3.connect(s.data_dir / "hader.db")) as db:
            assert db.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 41
    else:
        assert not (s.data_dir / "hader.db").exists()
        for suffix in ("-wal", "-shm", "-journal"):
            assert not (s.data_dir / ("hader.db" + suffix)).exists()
    assert not (s.data_dir / ".restore_journal.json").exists()


def test_relative_database_url_uses_same_path_as_sqlalchemy(recovery_data, tmp_path, monkeypatch):
    s = recovery_data
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "relative.sqlite"
    (s.data_dir / "hader.db").rename(database)
    monkeypatch.setattr(B, "settings", replace(s, database_url="sqlite:///relative.sqlite"))
    assert B._db_path() == database
    assert B.validate_snapshot(B.create_snapshot())["format"] == "snapshot_zip"


def test_sqlite_uri_snapshot_fails_explicitly_instead_of_selecting_wrong_file(recovery_data, monkeypatch):
    s = recovery_data
    url = f"sqlite:///file:{s.data_dir / 'hader.db'}?uri=true&mode=rwc"
    monkeypatch.setattr(B, "settings", replace(s, database_url=url))
    with pytest.raises(B.BackupError, match="URI"):
        B.create_snapshot()


@pytest.mark.parametrize("backup_format", ["snapshot_zip", "legacy_db"])
def test_restore_refuses_a_database_with_busy_wal_reader(recovery_data, backup_format):
    from contextlib import closing
    s = recovery_data
    database = s.data_dir / "hader.db"
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    if backup_format == "snapshot_zip":
        archive = B.create_snapshot()
    else:
        archive = s.data_dir / "legacy.db"
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(archive)) as target:
            source.backup(target)
    B.schedule_restore(archive, "admin")
    with closing(sqlite3.connect(database)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT first_name FROM employee").fetchall()
        with closing(sqlite3.connect(database)) as writer:
            writer.execute("UPDATE employee SET first_name='Committed WAL must survive'")
            writer.commit()
        result = B.apply_pending_restore()
        assert not result["ok"] and "busy" in result["error"].lower()
        assert _name(s) == "Committed WAL must survive"
        assert not (s.data_dir / ".restore_journal.json").exists()
        assert list(s.data_dir.glob(".hader_restore_*")) == []


def test_legacy_restore_staging_captures_committed_source_wal(recovery_data):
    from contextlib import closing
    s = recovery_data
    legacy = s.data_dir / "legacy.db"
    with closing(sqlite3.connect(s.data_dir / "hader.db")) as source, closing(sqlite3.connect(legacy)) as target:
        source.backup(target)
    with closing(sqlite3.connect(legacy)) as reader:
        assert reader.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        reader.execute("BEGIN")
        reader.execute("SELECT first_name FROM employee").fetchall()
        with closing(sqlite3.connect(legacy)) as writer:
            writer.execute("UPDATE employee SET first_name='Source committed WAL'")
            writer.commit()
        assert (s.data_dir / "legacy.db-wal").exists()
        B.schedule_restore(legacy, "admin")
        assert B.apply_pending_restore()["ok"]
        assert _name(s) == "Source committed WAL"
