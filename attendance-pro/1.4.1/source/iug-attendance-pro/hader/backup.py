"""Offline, integrity-checked snapshots and restart-safe restore.

Snapshots deliberately contain the database and the small set of files that make a
server installation reproducible.  They never contain logs, credentials copied from
the process, or another backup archive.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .config import settings
from .version import VERSION


class BackupError(ValueError):
    pass


MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_SAFE_MEMBER = re.compile(r"^(?:manifest\.json|hader\.db|network\.ini|timezone\.ini|storage\.ini|\.secret_key|photos/[A-Za-z0-9._/-]+)$")


def _db_path() -> Path:
    try:
        url = make_url(settings.database_url)
    except (ArgumentError, ValueError, TypeError) as exc:
        raise BackupError("invalid database URL") from exc
    if url.get_backend_name() != "sqlite":
        raise BackupError("snapshot currently supports SQLite only")
    if not url.database or url.database == ":memory:":
        raise BackupError("snapshot requires a file-backed SQLite database")
    if str(url.query.get("uri", "")).lower() in ("true", "1"):
        raise BackupError("snapshot does not support SQLite URI databases")
    # SQLAlchemy resolves a relative SQLite URL against the working directory.
    # Follow the engine's URL rather than silently selecting a different file.
    return Path(url.database).absolute()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_actor(actor: str) -> str:
    return " ".join(str(actor or "system").replace("\r", " ").replace("\n", " ").split())[:50] or "system"


def _snapshot_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = [("hader.db", _db_path())]
    for rel, path in (("network.ini", settings.data_dir / "network.ini"),
                      ("timezone.ini", settings.data_dir / "timezone.ini"),
                      ("storage.ini", settings.data_dir / "storage.ini"),
                      (".secret_key", settings.data_dir / ".secret_key")):
        if path.is_file():
            files.append((rel, path))
    if settings.photos_dir.is_dir():
        for p in sorted(settings.photos_dir.rglob("*")):
            if p.is_file() and ".." not in p.parts:
                files.append((p.relative_to(settings.data_dir).as_posix(), p))
    return files


def create_snapshot(label: str = "manual") -> Path:
    """Create a consistent ZIP snapshot and return its owner-readable path."""
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "manual"))[:40] or "manual"
    dest = settings.backups_dir / f"hader_{stamp}_{clean}.zip"
    db_path = _db_path()
    if not db_path.exists():
        raise BackupError("database does not exist")
    with tempfile.TemporaryDirectory(prefix="hader_snapshot_") as td:
        stable = Path(td) / "hader.db"
        with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(stable)) as dst:
            with dst:
                src.backup(dst)
        files = [("hader.db", stable)] + [(name, path) for name, path in _snapshot_files()
                                         if name != "hader.db" and path.is_file()]
        manifest = {
            "format": "snapshot_zip", "schema_version": 1, "app_version": VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": {},
        }
        tmp = dest.with_suffix(".tmp")
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for name, path in files:
                    digest, size = hashlib.sha256(), 0
                    with path.open("rb") as source, z.open(name, "w", force_zip64=True) as target:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            target.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    manifest["files"][name] = {"sha256": digest.hexdigest(), "size": size}
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode())
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    if os.name != "nt":
        os.chmod(dest, 0o600)
    return dest


def _validate_sqlite(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path)) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "employee" not in names or "audit_log" not in names:
                raise BackupError("database schema is not a Hader database")
            result = db.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("database integrity check failed")
    except (sqlite3.DatabaseError, OSError) as exc:
        raise BackupError("database is corrupt") from exc


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(value))[:3]) or (0,)


def validate_snapshot(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise BackupError("snapshot not found")
    if path.suffix.lower() == ".db":
        _validate_sqlite(path)
        return {"format": "legacy_db", "schema_version": 1, "path": str(path)}
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise BackupError("snapshot is too large")
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_ARCHIVE_BYTES or z.getinfo("manifest.json").file_size > 8 * 1024 * 1024:
                raise BackupError("snapshot is too large")
            names = z.namelist()
            if len(names) != len(set(names)) or not names or "manifest.json" not in names:
                raise BackupError("invalid snapshot members")
            for name in names:
                if not _SAFE_MEMBER.fullmatch(name) or "\\" in name or name.startswith("/") or ".." in name.split("/"):
                    raise BackupError("unsafe snapshot member")
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != "snapshot_zip" or manifest.get("schema_version") != 1:
                raise BackupError("unsupported snapshot format")
            if _version_tuple(manifest.get("app_version", "0")) > _version_tuple(VERSION):
                raise BackupError("snapshot was made by a newer application")
            listed = manifest.get("files")
            if not isinstance(listed, dict) or set(listed) != set(names) - {"manifest.json"} or "hader.db" not in listed:
                raise BackupError("snapshot manifest does not match its files")
            with tempfile.TemporaryDirectory(prefix="hader_validate_") as td:
                stable = Path(td) / "hader.db"
                for name, meta in listed.items():
                    digest, size = hashlib.sha256(), 0
                    target = stable.open("wb") if name == "hader.db" else None
                    try:
                        with z.open(name) as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                                size += len(chunk)
                                if target is not None:
                                    target.write(chunk)
                    finally:
                        if target is not None:
                            target.close()
                    if size != int(meta.get("size", -1)) or digest.hexdigest() != meta.get("sha256"):
                        raise BackupError("snapshot integrity check failed")
                # Close the extracted file before SQLite opens it. Windows does
                # not allow reopening a delete-on-close NamedTemporaryFile.
                _validate_sqlite(stable)
            return manifest | {"path": str(path)}
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("invalid snapshot archive") from exc


def schedule_restore(path: Path, actor: str) -> dict:
    """Validate and stage a restore.  The live database is untouched."""
    path = Path(path)
    meta = validate_snapshot(path)
    marker_path = settings.data_dir / ".pending_restore.json"
    if marker_path.exists() or (settings.data_dir / ".restore_journal.json").exists():
        raise BackupError("a restore or recovery is already pending")
    if path.suffix.lower() == ".db":
        # A legacy database is copied as an artifact as well, so deleting the original
        # cannot invalidate a scheduled restore.
        artifact_name = f".pending_restore_{os.urandom(8).hex()}.db"
    else:
        artifact_name = f".pending_restore_{os.urandom(8).hex()}.zip"
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    artifact = settings.backups_dir / artifact_name
    if meta["format"] == "legacy_db":
        # Include any committed WAL pages in a standalone staged artifact.
        _copy_database(path, artifact)
    else:
        shutil.copy2(path, artifact)
    if os.name != "nt":
        os.chmod(artifact, 0o600)
    marker = {"file": f"backups/{artifact_name}", "sha256": _sha_file(artifact),
              "format": meta["format"], "actor": _safe_actor(actor)}
    _write_json(marker_path, marker)
    return {"scheduled": True, "restart_required": True, "format": meta["format"]}


def _replace(source: Path, target: Path) -> None:
    os.replace(source, target)
    _sync_directory(source.parent)
    if source.parent != target.parent:
        _sync_directory(target.parent)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Some filesystems do not implement directory fsync.
        pass


def _write_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _audit_restore(actor: str, action: str = "restore") -> None:
    try:
        with closing(sqlite3.connect(_db_path())) as db:
            db.execute("INSERT INTO audit_log(username, action, target, detail, ip, created_at) VALUES(?,?,?,?,?,datetime('now'))",
                       (_safe_actor(actor), action, "system", "offline restore", ""))
            db.commit()
    except sqlite3.DatabaseError:
        pass


def _log(event: str, **data) -> None:
    rec = {"event": event, "at": datetime.now().isoformat(timespec="seconds"), **data}
    with open(settings.data_dir / "restore.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _copy_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        with dst:
            src.backup(dst)
    with target.open("rb+") as stable:
        os.fsync(stable.fileno())


def _install_legacy(source: Path, actor: str) -> None:
    _install_restore(source, actor, legacy=True)


def _restore_targets(legacy: bool = False) -> dict[str, Path]:
    if legacy:
        return {"hader.db": _db_path()}
    targets = {"hader.db": _db_path(), **{rel: (settings.data_dir / rel).absolute()
               for rel in ("photos", "network.ini", "timezone.ini", "storage.ini", ".secret_key")}}
    if len(set(targets.values())) != len(targets) or targets["photos"] in targets["hader.db"].parents:
        raise BackupError("database path overlaps another restore target")
    return targets


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _checkpoint_database(path: Path) -> None:
    if not path.exists():
        return
    with closing(sqlite3.connect(path, timeout=1)) as db:
        result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result and result[0]:
            raise BackupError("database is busy; stop other processes before restoring")


def _discard_database_sidecars(path: Path) -> None:
    # A snapshot inherits SQLite's WAL header. An interrupted audit can leave its
    # WAL behind; replaying it over a rolled-back original would corrupt data.
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    _sync_directory(path.parent)


def _cleanup_restore(journal: dict) -> None:
    for entry in journal["entries"]:
        stage = Path(entry["stage"])
        if stage.exists():
            shutil.rmtree(stage)
            _sync_directory(stage.parent)
    (settings.data_dir / ".restore_journal.json").unlink(missing_ok=True)
    _sync_directory(settings.data_dir)


def _rollback_restore(journal: dict) -> None:
    errors = []
    for entry in reversed(journal["entries"]):
        target = Path(entry["target"])
        old = Path(entry["stage"]) / "old" / entry["rel"]
        try:
            if old.exists() or old.is_symlink():
                # If this rename fails the saved original is retained in staging.
                # A later startup can retry from the journal; never auto-delete it.
                if entry["rel"] == "hader.db":
                    _discard_database_sidecars(target)
                _remove_path(target)
                _replace(old, target)
            elif not entry["existed"]:
                if entry["rel"] == "hader.db":
                    _discard_database_sidecars(target)
                _remove_path(target)
            elif not (target.exists() or target.is_symlink()):
                raise BackupError("original file is missing")
        except Exception as exc:
            errors.append(f"{entry['rel']}: {exc}")
    if errors:
        raise BackupError("restore recovery is incomplete; saved originals retained: " + "; ".join(errors))
    _cleanup_restore(journal)


def _read_restore_journal() -> dict:
    try:
        journal = json.loads((settings.data_dir / ".restore_journal.json").read_text(encoding="utf-8"))
        format_name = journal.get("format", "snapshot_zip")
        if format_name not in ("snapshot_zip", "legacy_db"):
            raise BackupError("invalid restore recovery format")
        targets = _restore_targets(legacy=format_name == "legacy_db")
        entries = journal["entries"]
        # Recover a pending 1.3 restore before adding the two new config targets.
        if format_name == "snapshot_zip" and {e["rel"] for e in entries} == {"hader.db", "photos", "network.ini", ".secret_key"}:
            targets = {rel: path for rel, path in targets.items() if rel not in {"timezone.ini", "storage.ini"}}
        if (journal.get("schema_version") != 1 or journal.get("phase") not in ("installing", "committed")
                or len(entries) != len(targets) or {e["rel"] for e in entries} != set(targets)):
            raise BackupError("invalid restore recovery journal")
        for entry in entries:
            target, stage = Path(entry["target"]), Path(entry["stage"])
            if (target != targets[entry["rel"]] or type(entry["existed"]) is not bool
                    or stage.parent != target.parent or not stage.name.startswith(".hader_restore_")
                    or stage.is_symlink()):
                raise BackupError("unsafe restore recovery journal")
        return journal
    except (KeyError, ValueError, TypeError, OSError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("invalid restore recovery journal") from exc


def _install_restore(source: Path, actor: str, *, legacy: bool = False) -> None:
    # Each target gets sibling staging on its own volume. Windows installations
    # can keep the database on D: while TEMP and the other data live on C:.
    journal = {"schema_version": 1, "phase": "installing",
               "format": "legacy_db" if legacy else "snapshot_zip", "entries": []}
    try:
        targets = _restore_targets(legacy=legacy)
        # Preserve original committed WAL changes in the main file before it is
        # saved. Every connection is closed before Windows renames any DB file.
        _checkpoint_database(targets["hader.db"])
        for rel, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".hader_restore_", dir=target.parent))
            (stage / "new").mkdir()
            (stage / "old").mkdir()
            journal["entries"].append({"rel": rel, "target": str(target), "stage": str(stage),
                                       "existed": target.exists() or target.is_symlink()})
        entries = {entry["rel"]: entry for entry in journal["entries"]}
        if legacy:
            _copy_database(source, Path(entries["hader.db"]["stage"]) / "new" / "hader.db")
        else:
            with zipfile.ZipFile(source) as z:
                for name in z.namelist():
                    if name == "manifest.json":
                        continue
                    rel = "photos" if name.startswith("photos/") else name
                    destination = Path(entries[rel]["stage"]) / "new" / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as zipped, destination.open("wb") as extracted:
                        shutil.copyfileobj(zipped, extracted, 1024 * 1024)
                        extracted.flush()
                        os.fsync(extracted.fileno())
        for entry in journal["entries"]:
            stage = Path(entry["stage"])
            directories = [stage] + [p for p in stage.rglob("*") if p.is_dir()]
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                _sync_directory(directory)
            _sync_directory(stage.parent)
        _write_json(settings.data_dir / ".restore_journal.json", journal)
    except Exception:
        # Preparation cannot have moved live files yet.
        for entry in journal["entries"]:
            shutil.rmtree(entry["stage"], ignore_errors=True)
        raise
    try:
        for entry in journal["entries"]:
            if entry["existed"]:
                _replace(Path(entry["target"]), Path(entry["stage"]) / "old" / entry["rel"])
            if entry["rel"] == "hader.db":
                _discard_database_sidecars(Path(entry["target"]))
        for entry in journal["entries"]:
            staged = Path(entry["stage"]) / "new" / entry["rel"]
            if staged.exists():
                _replace(staged, Path(entry["target"]))
        _audit_restore(actor)
        journal["phase"] = "committed"
        _write_json(settings.data_dir / ".restore_journal.json", journal)
    except Exception:
        _rollback_restore(journal)
        raise
    # Cleanup runs only after the durable commit marker. If interrupted here,
    # recovery keeps the installed snapshot instead of rolling it back.
    _cleanup_restore(journal)


def _install_snapshot(source: Path, actor: str) -> None:
    _install_restore(source, actor)


def apply_pending_restore() -> dict | None:
    marker_path = settings.data_dir / ".pending_restore.json"
    journal_path = settings.data_dir / ".restore_journal.json"
    if journal_path.exists():
        try:
            journal = _read_restore_journal()
            committed = journal["phase"] == "committed"
            if committed:
                _cleanup_restore(journal)
            else:
                _rollback_restore(journal)
            marker_path.unlink(missing_ok=True)
            _log("restore.recovered", rolled_back=not committed)
            return {"ok": True, "recovered": True, "rolled_back": not committed}
        except Exception as exc:
            _log("restore.recovery_failed", error=str(exc)[:300])
            return {"ok": False, "recovery_required": True, "error": str(exc)[:300]}
    if not marker_path.exists():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not re.fullmatch(r"backups/\.pending_restore_[0-9a-f]{16}\.(?:db|zip)", str(marker.get("file", ""))):
            raise BackupError("invalid pending restore artifact path")
        artifact = settings.data_dir / marker["file"]
        if _sha_file(artifact) != marker.get("sha256"):
            raise BackupError("pending restore artifact was changed")
        validate_snapshot(artifact)
        before = create_snapshot("before_restore") if _db_path().exists() else None
        if marker.get("format") == "legacy_db":
            _install_legacy(artifact, marker.get("actor", "system"))
        else:
            _install_snapshot(artifact, marker.get("actor", "system"))
        _log("restore.completed", actor=_safe_actor(marker.get("actor", "system")), backup=before.name if before else "")
        artifact.unlink(missing_ok=True); marker_path.unlink(missing_ok=True)
        return {"ok": True, "backup": before.name if before else ""}
    except Exception as exc:
        _log("restore.failed", error=str(exc)[:300])
        # A failed artifact remains available for diagnosis, but the marker is removed
        # so a broken server cannot loop forever on every startup.
        recovery_required = journal_path.exists()
        if not recovery_required:
            marker_path.unlink(missing_ok=True)
        return {"ok": False, "recovery_required": recovery_required, "error": str(exc)[:300]}

