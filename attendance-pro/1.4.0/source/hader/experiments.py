"""Bounded, cancellable experiment processes; live attendance data is never opened here."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .experiment_config import validate_options

MAX_JOBS = 10
TIMEOUT_SECONDS = 20 * 60
ACTIVE = {"queued", "running"}
_ID = re.compile(r"^[0-9a-f]{32}$")


class ExperimentError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".status_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ExperimentManager:
    def __init__(self, root: Path, worker_path: Path | None = None):
        self.root = Path(root)
        self.worker_path = worker_path or Path(__file__).resolve().parent.parent / "tools" / "experiment_worker.py"
        self._lock = threading.RLock()
        self._processes: dict[str, tuple[subprocess.Popen, float]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._closed = False
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Supervised workers stop when their owner's pipe closes, including a
        # server crash. Recover metadata and discard unfinished synthetic data.
        for folder in self._folders():
            metadata = _read_json(folder / "job.json")
            if metadata.get("status") in ACTIVE:
                metadata.update(status="interrupted", error="توقف الخادم أثناء التجربة / Server stopped during the experiment")
                _write_json(folder / "job.json", metadata)
            if metadata.get("status") == "interrupted":
                self._clean_interrupted(folder)

    def _folders(self):
        return [p for p in self.root.iterdir() if _ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]

    def _folder(self, job_id: str) -> Path:
        if not _ID.fullmatch(job_id or ""):
            raise ExperimentError("التجربة غير موجودة / Experiment not found", 404)
        folder = self.root / job_id
        if folder.is_symlink() or not folder.is_dir() or not (folder / "job.json").is_file():
            raise ExperimentError("التجربة غير موجودة / Experiment not found", 404)
        return folder

    def _finish(self, job_id: str, process: subprocess.Popen, reason: str | None = None) -> None:
        folder = self._folder(job_id)
        metadata = _read_json(folder / "job.json")
        result = _read_json(folder / "result.json")
        if reason:
            metadata.update(status="failed", error=reason)
        elif process.returncode == 0 and result.get("status") == "passed":
            metadata.update(status="passed")
        else:
            metadata.update(status="failed", error=str(result.get("error") or "تعذر إكمال التجربة / Experiment could not finish")[:500])
        _write_json(folder / "job.json", metadata)
        self._processes.pop(job_id, None)
        timer = self._timers.pop(job_id, None)
        if timer:
            timer.cancel()
        if process.stdin:
            process.stdin.close()
        if metadata["status"] == "failed":
            self._remove_working_files(folder)

    def _expire(self, job_id: str) -> None:
        with self._lock:
            record = self._processes.get(job_id)
            if record:
                process = record[0]
                if process.poll() is None:
                    self._stop_process(process)
                    self._finish(job_id, process, "انتهت مدة التجربة (20 دقيقة) / Experiment exceeded 20 minutes")
                else:
                    self._finish(job_id, process)

    def _refresh(self) -> None:
        # A crashed worker may still hold its Windows SQLite handle briefly at
        # startup. Retry disposal after it observes EOF and exits.
        for folder in self._folders():
            if _read_json(folder / "job.json").get("status") == "interrupted":
                self._clean_interrupted(folder)
        for job_id, (process, started) in list(self._processes.items()):
            if process.poll() is not None:
                self._finish(job_id, process)
            elif time.monotonic() - started > TIMEOUT_SECONDS:
                self._stop_process(process)
                self._finish(job_id, process, "انتهت مدة التجربة (20 دقيقة) / Experiment exceeded 20 minutes")

    def _public(self, folder: Path) -> dict:
        metadata = _read_json(folder / "job.json")
        job = {key: metadata.get(key) for key in ("id", "status", "created_at", "config")}
        job["progress"] = _read_json(folder / "progress.json") or {
            "stage": "queued", "message": "جارٍ بدء التجربة / Starting", "percent": 0}
        if metadata.get("error"):
            job["error"] = metadata["error"]
        if job["status"] == "passed":
            job["result"] = _read_json(folder / "result.json")
            job["preview"] = _read_json(folder / "preview.json")
        job["downloads"] = {"json": job["status"] == "passed" and (folder / "result.json").is_file(),
                            "csv": job["status"] == "passed" and (folder / "transactions.csv").is_file()}
        return job

    def list(self) -> list[dict]:
        with self._lock:
            self._refresh()
            jobs = [self._public(folder) for folder in self._folders()]
            return sorted(jobs, key=lambda job: job.get("created_at") or "", reverse=True)

    def get(self, job_id: str) -> dict:
        with self._lock:
            self._refresh()
            return self._public(self._folder(job_id))

    def start(self, options: dict, on_accept=None) -> dict:
        config = validate_options(options)
        with self._lock:
            self._refresh()
            if self._closed:
                raise ExperimentError("الخادم يتوقف؛ حاول بعد إعادة التشغيل / Server is stopping")
            if self._processes:
                raise ExperimentError("تجربة تعمل بالفعل؛ انتظر أو أوقفها / An experiment is already running")
            if len(self._folders()) >= MAX_JOBS:
                raise ExperimentError("حُفظت عشر تجارب؛ احذف تجربة قديمة أولاً / Delete an old experiment before starting another")
            if not self.worker_path.is_file():
                raise ExperimentError("ملف تشغيل التجربة غير موجود / Experiment worker is missing", 503)
            job_id = uuid.uuid4().hex
            folder = self.root / job_id
            folder.mkdir(mode=0o700)
            metadata = {"id": job_id, "status": "queued", "created_at": datetime.now(timezone.utc).isoformat(),
                        "config": config}
            try:
                _write_json(folder / "config.json", config)
                _write_json(folder / "job.json", metadata)
                if on_accept:
                    on_accept()
                with (folder / "worker.log").open("wb") as log:
                    try:
                        process = subprocess.Popen([sys.executable, str(self.worker_path), "--job-dir", str(folder), "--supervised"],
                            cwd=self.worker_path.parent.parent, stdin=subprocess.PIPE, stdout=log,
                            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    except OSError as exc:
                        raise ExperimentError("تعذر بدء عملية التجربة؛ تحقق من بيئة Python / Could not start the experiment process; check the Python environment", 503) from exc
            except Exception:
                shutil.rmtree(folder)
                raise
            self._processes[job_id] = (process, time.monotonic())
            metadata["status"] = "running"
            _write_json(folder / "job.json", metadata)
            timer = self._timers[job_id] = threading.Timer(TIMEOUT_SECONDS, self._expire, args=(job_id,))
            timer.daemon = True
            timer.start()
            return self._public(folder)

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            self._refresh()
            folder = self._folder(job_id)
            record = self._processes.pop(job_id, None)
            if record:
                self._stop_process(record[0])
                timer = self._timers.pop(job_id, None)
                if timer:
                    timer.cancel()
                if record[0].stdin:
                    record[0].stdin.close()
                metadata = _read_json(folder / "job.json")
                metadata.update(status="cancelled")
                _write_json(folder / "job.json", metadata)
                _write_json(folder / "progress.json", {"stage": "cancelled", "message": "أُوقفت التجربة / Experiment stopped", "percent": 0})
                self._remove_working_files(folder)
            return self._public(folder)

    @staticmethod
    def _remove_working_files(folder: Path) -> None:
        # Only the experiment's disposable workspace; never a supplied data path.
        for path in folder.glob("experiment_*"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        for name in ("transactions.csv", "preview.json", "result.json"):
            (folder / name).unlink(missing_ok=True)

    def _clean_interrupted(self, folder: Path) -> None:
        try:
            self._remove_working_files(folder)
        except OSError:
            # Retry during refresh; Windows can still be releasing old handles.
            pass

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._refresh()
            folder = self._folder(job_id)
            if job_id in self._processes:
                raise ExperimentError("أوقف التجربة قبل حذفها / Stop the experiment before deleting it")
            shutil.rmtree(folder)

    def download_path(self, job_id: str, kind: str) -> Path:
        with self._lock:
            self._refresh()
            folder = self._folder(job_id)
            name = {"json": "result.json", "csv": "transactions.csv"}.get(kind)
            metadata = _read_json(folder / "job.json")
            path = folder / name if name else None
            if metadata.get("status") != "passed" or path is None or not path.is_file() or path.is_symlink():
                raise ExperimentError("الملف غير متاح / File not available", 404)
            return path

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            for job_id in list(self._processes):
                self.cancel(job_id)


_MANAGERS: dict[Path, ExperimentManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_manager() -> ExperimentManager:
    from .config import settings
    root = settings.data_dir / "experiments"
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(root)
        if manager is None or manager._closed:
            manager = _MANAGERS[root] = ExperimentManager(root)
        return manager


def shutdown_managers() -> None:
    with _MANAGERS_LOCK:
        for manager in _MANAGERS.values():
            manager.shutdown()
        _MANAGERS.clear()
