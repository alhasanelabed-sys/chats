"""Read-only clock checks and bounded, isolated backup recovery rehearsals."""
from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import settings

_STORAGE_OVERRIDE: dict[str, str] = {}
_MANAGERS: dict[str, "BackupCheckManager"] = {}
_MANAGERS_LOCK = threading.Lock()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def validate_synchronous(value: str) -> str:
    if not isinstance(value, str) or value.upper().strip() not in {"NORMAL", "FULL"}:
        raise ValueError("الحفظ يجب أن يكون NORMAL أو FULL / Durability must be NORMAL or FULL")
    return value.upper().strip()


def configure_synchronous(value: str, persist: bool = True) -> str:
    value = validate_synchronous(value)
    path = settings.data_dir / "storage.ini"
    if persist:
        cp = configparser.ConfigParser(interpolation=None)
        cp["storage"] = {"synchronous": value}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".storage_", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                cp.write(stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        _STORAGE_OVERRIDE.pop(str(path), None)
    else:
        _STORAGE_OVERRIDE[str(path)] = value
    return value


def synchronous_mode() -> str:
    value = os.getenv("HADER_STORAGE_SYNCHRONOUS")
    if value is not None:
        return validate_synchronous(value)
    path = settings.data_dir / "storage.ini"
    if str(path) in _STORAGE_OVERRIDE:
        return _STORAGE_OVERRIDE[str(path)]
    cp = configparser.ConfigParser(interpolation=None)
    if path.exists():
        cp.read(path, encoding="utf-8")
    return validate_synchronous(cp.get("storage", "synchronous", fallback="NORMAL"))


def validate_offsite_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 1024 or any(c in value for c in "\x00\r\n"):
        raise ValueError("مسار النسخة الخارجية غير صالح / Invalid external backup folder")
    value = value.strip()
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("اختر مساراً كاملاً لمجلد خارج بيانات البرنامج / Use an absolute external backup folder")
    resolved = path.resolve()
    if resolved == settings.data_dir.resolve() or settings.data_dir.resolve() in resolved.parents:
        raise ValueError("المجلد الخارجي يجب أن يكون خارج مجلد بيانات البرنامج / External folder must be outside application data")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("المسار ليس مجلداً / The external path is not a folder")
    return str(resolved)


class OperationError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class BackupCheckManager:
    TIMEOUT_SECONDS = 20 * 60

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "backup_checks"
        self.state_path = Path(data_dir) / "backup_check.json"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.process = None
        self.timer = None
        self.folder = None
        self.closed = False
        previous = _read_json(self.state_path)
        if previous.get("status") == "running":
            previous.update(status="interrupted", error="توقف التحقق عند إغلاق البرنامج / Verification was interrupted")
            _write_json(self.state_path, previous)
        for folder in self.root.glob("check_*"):
            if folder.is_dir() and not folder.is_symlink():
                self._cleanup_folder(folder)

    @staticmethod
    def _cleanup_folder(folder: Path):
        config = _read_json(folder / "config.json")
        offsite, name = config.get("offsite"), config.get("archive_name")
        if isinstance(offsite, str) and isinstance(name, str) and Path(name).name == name:
            try:
                external = validate_offsite_path(offsite)
                if external:
                    (Path(external) / ("." + name + "." + folder.name + ".tmp" + Path(name).suffix.lower())).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        shutil.rmtree(folder, ignore_errors=True)

    def _cleanup(self):
        if self.folder is not None:
            self._cleanup_folder(self.folder)
            self.folder = None

    def _finish(self, forced: str | None = None):
        previous = _read_json(self.state_path)
        result = _read_json(self.folder / "result.json") if self.folder else {}
        status = forced or ("passed" if self.process.returncode == 0 and result.get("status") == "passed" else "failed")
        previous.update({key: value for key, value in result.items() if key in {
            "seconds", "counts", "offsite_copy_verified", "integrity", "manifest_verified", "error", "checked_at"}})
        previous.update(status=status, restore_apply_performed=False)
        if status == "failed" and not previous.get("error"):
            previous["error"] = "تعذر التحقق من النسخة / Backup verification failed"
        if status == "cancelled":
            previous["error"] = "أُلغي التحقق / Verification cancelled"
        _write_json(self.state_path, previous)
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.process and self.process.stdin:
            self.process.stdin.close()
        self.process = None
        self._cleanup()

    def _refresh(self):
        if self.process is not None and self.process.poll() is not None:
            self._finish()

    def _stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)

    def _expire(self):
        with self.lock:
            if self.process is None:
                return
            self._refresh()
            if self.process is not None:
                self._stop()
                _write_json(self.folder / "result.json", {"error": "انتهت مهلة التحقق / Verification timed out"})
                self._finish("failed")

    def status(self) -> dict:
        with self.lock:
            self._refresh()
            state = _read_json(self.state_path)
            return state or {"status": "idle", "restore_apply_performed": False}

    def start(self, archive: Path, offsite: str = "") -> dict:
        archive = Path(archive)
        offsite = validate_offsite_path(offsite)
        with self.lock:
            self._refresh()
            if self.closed:
                raise OperationError("التطبيق يغلق / Application is shutting down", 503)
            if self.process is not None:
                raise OperationError("يوجد تحقق جارٍ / A verification is already running")
            if not archive.is_file() or archive.is_symlink() or archive.suffix.lower() not in {".zip", ".db"}:
                raise OperationError("نسخة غير موجودة أو غير صالحة / Backup not found", 404)
            worker = Path(__file__).resolve().parent.parent / "tools" / "backup_check_worker.py"
            if not worker.is_file():
                raise OperationError("عامل التحقق غير متاح / Verification worker is unavailable", 503)
            folder = Path(tempfile.mkdtemp(prefix="check_", dir=self.root))
            staged = folder / ("snapshot" + archive.suffix.lower())
            try:
                # Preserve an immutable inode while normal retention removes the
                # original name. The scratch folder stays on the same filesystem.
                try:
                    os.link(archive, staged)
                except OSError:
                    shutil.copyfile(archive, staged)
                _write_json(folder / "config.json", {"source": str(staged), "archive_name": archive.name,
                                                       "offsite": offsite})
                with (folder / "worker.log").open("wb") as log:
                    self.process = subprocess.Popen(
                        [sys.executable, str(worker), "--job-dir", str(folder), "--supervised"],
                        stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                        cwd=worker.parent.parent,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.folder = folder
                from .timekeeping import now_local
                state = {"status": "running", "archive": archive.name,
                         "started_at": now_local().isoformat(timespec="seconds"),
                         "offsite_enabled": bool(offsite), "restore_apply_performed": False}
                _write_json(self.state_path, state)
                self.timer = threading.Timer(self.TIMEOUT_SECONDS, self._expire)
                self.timer.daemon = True
                self.timer.start()
                child = self.process
                def monitor():
                    child.wait()
                    with self.lock:
                        if self.process is child:
                            self._refresh()
                threading.Thread(target=monitor, daemon=True).start()
                return state
            except Exception:
                if self.process is not None:
                    self._stop()
                    if self.process.stdin:
                        self.process.stdin.close()
                    self.process = None
                shutil.rmtree(folder, ignore_errors=True)
                self.folder = None
                raise

    def cancel(self) -> dict:
        with self.lock:
            self._refresh()
            if self.process is not None:
                self._stop()
                self._finish("cancelled")
            return self.status()

    def shutdown(self):
        with self.lock:
            self.cancel()
            self.closed = True


def backup_manager() -> BackupCheckManager:
    key = str(settings.data_dir.resolve())
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None or manager.closed:
            manager = BackupCheckManager(settings.data_dir)
            _MANAGERS[key] = manager
        return manager


def after_backup(path: Path) -> dict:
    from . import store
    from .db import session_scope
    with session_scope() as db:
        offsite = store.get(db, "backup.offsite_path", "") or ""
        verify = bool(store.get(db, "backup.verify_after", True))
    if not verify and not offsite:
        state = {"status": "disabled", "archive": Path(path).name, "restore_apply_performed": False}
        _write_json(settings.data_dir / "backup_check.json", state)
        return state
    try:
        return backup_manager().start(path, offsite)
    except OperationError as exc:
        return {"status": "busy" if exc.status == 409 else "failed", "error": str(exc)}
    except (OSError, ValueError):
        # A completed local backup remains useful when the external destination
        # is unavailable. This failure never discards the original artifact.
        state = {"status": "failed", "archive": Path(path).name,
                 "error": "تعذر بدء التحقق الخارجي / Could not start external verification",
                 "restore_apply_performed": False}
        _write_json(settings.data_dir / "backup_check.json", state)
        return state


def shutdown_operations():
    with _MANAGERS_LOCK:
        for manager in list(_MANAGERS.values()):
            manager.shutdown()
        _MANAGERS.clear()


def _clock_file(sn: str) -> Path:
    import hashlib
    return settings.data_dir / "device_clocks" / (hashlib.sha256(sn.encode()).hexdigest() + ".json")


def clock_status(sn: str) -> dict:
    return _read_json(_clock_file(sn)) or {"status": "unknown", "measured": False}


def read_clock(ip: str, port: int, comm_key: str) -> datetime:
    from .terminal.client import TerminalClient
    terminal = TerminalClient(ip, int(port or 4370), comm_key or 0, timeout=5).connect()
    try:
        return terminal.get_time()
    finally:
        terminal.disconnect()


def check_clock(device) -> dict:
    from .timekeeping import now_local, wall_time_info
    if not device.ip:
        raise OperationError("عنوان الجهاز مطلوب للقراءة المباشرة / Device IP is needed for a direct clock read", 422)
    # Share the existing socket lock with scheduled TCP pulls and controls.
    from .tcp_pull import _lock
    lock = _lock(device.sn)
    if not lock.acquire(blocking=False):
        raise OperationError("فحص ساعة الجهاز جارٍ / Device clock check is running")
    try:
        started, before = time.monotonic(), now_local()
        try:
            stamp = read_clock(device.ip, device.tcp_port, device.comm_key)
            after = now_local()
            midpoint = before + (after - before) / 2
            drift = round((stamp - midpoint).total_seconds(), 2)
            info = wall_time_info(stamp)
            state = {"status": "ok" if abs(drift) <= 60 else "drift", "measured": True,
                     "checked_at": after.isoformat(timespec="seconds"), "device_time": stamp.isoformat(timespec="seconds"),
                     "server_time": midpoint.isoformat(timespec="seconds"), "drift_seconds": drift,
                     "round_trip_seconds": round(time.monotonic() - started, 3), "wall_time_kind": info["kind"],
                     "clock_modified": False}
        except Exception:
            state = {"status": "error", "measured": False, "checked_at": now_local().isoformat(timespec="seconds"),
                     "error": "تعذرت قراءة ساعة الجهاز؛ تحقق من الاتصال والصلاحيات / Clock read failed; check connection and access",
                     "clock_modified": False}
        _write_json(_clock_file(device.sn), state)
        return state
    finally:
        lock.release()


def health(db) -> dict:
    from sqlalchemy import select
    from . import models as m, store
    from .db import now
    from .timekeeping import zone_name
    from .calculation_cache import stats as cache_stats
    from .api.devices import is_online
    stamp = now()
    usage = shutil.disk_usage(settings.data_dir)
    configured = synchronous_mode()
    active, journal = "unknown", "unknown"
    if db.bind.dialect.name == "sqlite":
        values = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
        active = values.get(db.connection().exec_driver_sql("PRAGMA synchronous").scalar(), "unknown")
        journal = db.connection().exec_driver_sql("PRAGMA journal_mode").scalar()
    rows = []
    for device in db.scalars(select(m.Device).order_by(m.Device.alias, m.Device.sn)).all():
        rows.append({"id": device.id, "sn": device.sn, "label": device.label, "enabled": device.enabled,
                     "online": is_online(device), "last_contact": device.last_activity.isoformat(sep=" ") if device.last_activity else None,
                     "last_upload": device.last_sync.isoformat(sep=" ") if device.last_sync else None,
                     "upload_age_seconds": max(0, int((stamp - device.last_sync).total_seconds())) if device.last_sync else None,
                     "clock": clock_status(device.sn)})
    backup = backup_manager().status()
    backup["enabled"] = bool(store.get(db, "backup.verify_after", True) or store.get(db, "backup.offsite_path", ""))
    backup["offsite_enabled"] = bool(store.get(db, "backup.offsite_path", ""))
    backup["physical_offsite_verified"] = False
    return {"time": {"zone": zone_name(), "server_time": stamp.isoformat(sep=" ")},
            "storage": {"configured_synchronous": configured, "active_synchronous": active,
                        "journal_mode": journal, "restart_required": active != configured,
                        "power_loss_tested": False},
            "disk": {"free_bytes": usage.free, "total_bytes": usage.total}, "devices": rows, "backup": backup,
            "calculation_cache": cache_stats()}
