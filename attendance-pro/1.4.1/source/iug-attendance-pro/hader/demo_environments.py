"""Manage isolated, disposable full application demos in supervised child processes."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .experiments import ExperimentError, _read_json, _write_json

MAX_SAVED = 3
SEED_TIMEOUT_SECONDS = 20 * 60
SESSION_SECONDS = 2 * 60 * 60
LIMITS = {"employees": 50000, "devices": 100, "days": 365, "punches": 2000000, "max_saved": MAX_SAVED}
DEFAULTS = {"employees": 1000, "devices": 4, "days": 14}
ACTIVE = {"starting", "running"}
_ID = re.compile(r"^[0-9a-f]{32}$")


def validate_options(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("خيارات التجربة غير صالحة / Invalid demo options")
    options = {**DEFAULTS, **data}
    for key, number in options.items():
        if type(number) is not int or not 1 <= number <= LIMITS[key]:
            raise ValueError(f"{key}: 1–{LIMITS[key]}")
    if options["devices"] > options["employees"]:
        raise ValueError("عدد الأجهزة يجب ألا يتجاوز الموظفين / Devices cannot exceed employees")
    if options["employees"] * options["days"] * 2 > LIMITS["punches"]:
        raise ValueError("الحد الأقصى مليونان حركة / Maximum two million punches")
    return options


class DemoManager:
    def __init__(self, root: Path, worker_path: Path | None = None, host: str = "127.0.0.1", time_zone: str = ""):
        self.root = Path(root)
        self.worker_path = worker_path or Path(__file__).resolve().parents[1] / "tools" / "demo_environment_worker.py"
        self.host = host
        self.time_zone = time_zone
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._processes = {}
        self._timers = {}
        self._closed = False
        for folder in self._folders():
            metadata = _read_json(folder / "job.json")
            if metadata.get("status") in ACTIVE:
                metadata.update(status="interrupted", error="توقف الخادم أثناء التجربة / Server stopped during demo")
                _write_json(folder / "job.json", metadata)
            (folder / "access.json").unlink(missing_ok=True)

    def _folders(self):
        return [p for p in self.root.iterdir() if _ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]

    def _folder(self, job_id):
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise ExperimentError("البيئة التجريبية غير موجودة / Demo not found", 404)
        folder = self.root / job_id
        if not folder.is_dir() or folder.is_symlink() or not (folder / "job.json").is_file():
            raise ExperimentError("البيئة التجريبية غير موجودة / Demo not found", 404)
        return folder

    def _public(self, folder):
        metadata = _read_json(folder / "job.json")
        out = {key: metadata.get(key) for key in ("id", "status", "created_at", "config", "expires_at", "error")}
        out["progress"] = _read_json(folder / "progress.json") or {"stage": "starting", "percent": 0, "message": "جارٍ إنشاء البيئة / Creating demo"}
        ready = _read_json(folder / "ready.json")
        out["counts"] = ready.get("counts", {})
        out["isolated"] = True
        out["physical_device_access"] = False
        if out["status"] == "running":
            out["port"] = ready.get("port")
            out["access_token"] = _read_json(folder / "access.json").get("token", "")
        return out

    def _finalize(self, job_id, status="stopped", error=""):
        process, _, _ = self._processes.pop(job_id)
        if process.stdin:
            process.stdin.close()
        timer = self._timers.pop(job_id, None)
        if timer:
            timer.cancel()
        folder = self._folder(job_id)
        metadata = _read_json(folder / "job.json")
        metadata.update(status=status)
        if error:
            metadata["error"] = error
        _write_json(folder / "job.json", metadata)
        (folder / "access.json").unlink(missing_ok=True)
        _write_json(folder / "progress.json", {"stage": status, "percent": 100 if status == "stopped" else 0,
                    "message": error or "أُوقفت البيئة التجريبية / Demo stopped"})

    def _refresh(self):
        for job_id, (process, started, deadline) in list(self._processes.items()):
            folder = self._folder(job_id)
            if process.poll() is not None:
                error = _read_json(folder / "error.json").get("error")
                self._finalize(job_id, "failed", error or "توقفت عملية التجربة / Demo process stopped")
            elif time.monotonic() > deadline:
                self._stop_process(process)
                self._finalize(job_id, "stopped", "انتهت مدة البيئة التجريبية / Demo session expired")
            elif (folder / "ready.json").is_file():
                metadata = _read_json(folder / "job.json")
                if metadata.get("status") == "starting":
                    metadata["status"] = "running"
                    _write_json(folder / "job.json", metadata)
            elif time.monotonic() - started > SEED_TIMEOUT_SECONDS:
                self._stop_process(process)
                self._finalize(job_id, "failed", "تجاوز إنشاء البيانات 20 دقيقة / Demo seeding exceeded 20 minutes")

    def _tick(self, job_id):
        with self._lock:
            self._refresh()
            if job_id in self._processes:
                timer = self._timers[job_id] = threading.Timer(1, self._tick, args=(job_id,))
                timer.daemon = True
                timer.start()

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def list(self):
        with self._lock:
            self._refresh()
            return sorted((self._public(f) for f in self._folders()), key=lambda job: job["created_at"] or "", reverse=True)

    def get(self, job_id):
        with self._lock:
            self._refresh()
            return self._public(self._folder(job_id))

    def start(self, data, on_accept=None):
        options = validate_options(data)
        with self._lock:
            self._refresh()
            if self._closed or self._processes:
                raise ExperimentError("أوقف البيئة الحالية أولاً / Stop the current demo first")
            if len(self._folders()) >= MAX_SAVED:
                raise ExperimentError("احذف بيئة قديمة أولاً / Delete an old demo first")
            if not self.worker_path.is_file():
                raise ExperimentError("ملف تشغيل البيئة غير موجود / Demo worker is missing", 503)
            job_id = uuid.uuid4().hex
            folder = self.root / job_id
            folder.mkdir(mode=0o700)
            created = datetime.now(timezone.utc)
            metadata = {"id": job_id, "status": "starting", "created_at": created.isoformat(),
                        "expires_at": (created + timedelta(seconds=SESSION_SECONDS)).isoformat(), "config": options}
            try:
                _write_json(folder / "job.json", metadata)
                _write_json(folder / "config.json", {**options, "host": self.host, "id": job_id,
                            "expires_at": metadata["expires_at"], "time_zone": self.time_zone})
                _write_json(folder / "access.json", {"token": secrets.token_urlsafe(32)})
                if os.name != "nt":
                    (folder / "access.json").chmod(0o600)
                if on_accept:
                    on_accept()
                with (folder / "worker.log").open("wb") as log:
                    clean = {k: v for k, v in os.environ.items() if not k.startswith("HADER_")}
                    process = subprocess.Popen([sys.executable, str(self.worker_path), "--job-dir", str(folder), "--supervised"],
                        cwd=self.worker_path.parent.parent, env=clean, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                shutil.rmtree(folder)
                raise ExperimentError("تعذر تشغيل البيئة التجريبية / Could not start demo", 503) from exc
            except Exception:
                shutil.rmtree(folder)
                raise
            started = time.monotonic()
            self._processes[job_id] = (process, started, started + SESSION_SECONDS)
            timer = self._timers[job_id] = threading.Timer(1, self._tick, args=(job_id,))
            timer.daemon = True
            timer.start()
            return self._public(folder)

    def stop(self, job_id):
        with self._lock:
            self._refresh()
            folder = self._folder(job_id)
            record = self._processes.get(job_id)
            if record:
                self._stop_process(record[0])
                self._finalize(job_id)
            return self._public(folder)

    def delete(self, job_id):
        with self._lock:
            self._refresh()
            if job_id in self._processes:
                raise ExperimentError("أوقف البيئة قبل حذفها / Stop demo before deletion")
            shutil.rmtree(self._folder(job_id))

    def shutdown(self):
        with self._lock:
            self._closed = True
            for job_id in list(self._processes):
                self.stop(job_id)


_MANAGERS = {}
_MANAGER_LOCK = threading.RLock()


def get_manager():
    from .config import settings
    root = settings.data_dir / "demo-environments"
    with _MANAGER_LOCK:
        manager = _MANAGERS.get(root)
        if manager is None or manager._closed:
            from .timekeeping import zone_name
            manager = _MANAGERS[root] = DemoManager(root, host=settings.host, time_zone=zone_name())
        else:
            from .timekeeping import zone_name
            manager.time_zone = zone_name()
        return manager


def shutdown_demo_managers():
    with _MANAGER_LOCK:
        for manager in _MANAGERS.values():
            manager.shutdown()
        _MANAGERS.clear()
