"""Private, owned report exports in bounded, supervised child processes."""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

MAX_ACTIVE = 2
MAX_SAVED = 10
TIMEOUT_SECONDS = 20 * 60
MAX_FILE_BYTES = 1024 * 1024 * 1024
ACTIVE = {"queued", "running"}
_ID = re.compile(r"^[0-9a-f]{32}$")
_IDS = re.compile(r"^[1-9][0-9]{0,9}(?:,[1-9][0-9]{0,9})*$")
_KEYS = {"transactions", "first_last", "time_card", "daily", "late", "early", "absent",
         "overtime", "exception", "leave", "summary", "monthly_status", "department"}
_OPTIONS = {"key", "start", "end", "employee_ids", "department_ids", "device", "lang", "q", "status", "fmt"}


class ReportJobError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def validate_options(data: dict) -> dict:
    """Reject unsupported filters rather than silently exporting a broader report."""
    if not isinstance(data, dict) or set(data) - _OPTIONS:
        raise ValueError("خيارات التقرير غير صالحة / Invalid report options")
    config = {"key": "daily", "employee_ids": "", "department_ids": "", "device": "",
              "lang": "ar", "q": "", "status": "", "fmt": "csv", **data}
    if any(not isinstance(value, str) for value in config.values()):
        raise ValueError("خيارات التقرير يجب أن تكون نصوصاً / Report options must be strings")
    if config["key"] not in _KEYS or config["lang"] not in {"ar", "en"} or config["fmt"] not in {"csv", "xlsx"}:
        raise ValueError("نوع التقرير أو اللغة أو الصيغة غير صالح / Invalid report, language or format")
    try:
        first, last = date.fromisoformat(config.get("start", "")), date.fromisoformat(config.get("end", ""))
        if first.isoformat() != config["start"] or last.isoformat() != config["end"]:
            raise ValueError()
        if last < first or (last - first).days >= 400 or last == date.max:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("حدد فترة صحيحة لا تتجاوز 400 يوم / Select a valid range of at most 400 days") from None
    for name in ("employee_ids", "department_ids"):
        value = config[name]
        if len(value) > 22000 or (value and not _IDS.fullmatch(value)) or (value and len(value.split(",")) > 2000):
            raise ValueError("قائمة الموظفين أو الأقسام غير صالحة / Invalid employee or department IDs")
        if value:
            config[name] = ",".join(dict.fromkeys(value.split(",")))
    for name, limit in (("device", 100), ("q", 100), ("status", 40)):
        value = config[name]
        if len(value) > limit or any(ord(char) < 32 for char in value):
            raise ValueError("فلتر التقرير غير صالح أو طويل جداً / Invalid or overlong report filter")
    return config


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
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ReportJobManager:
    def __init__(self, root: Path, database_url: str, worker_path: Path | None = None):
        self.root = Path(root)
        self.database_url = database_url
        self.worker_path = worker_path or Path(__file__).resolve().parent.parent / "tools" / "report_export_worker.py"
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._closed = False
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for folder in self._folders():
            metadata = _read_json(folder / "job.json")
            if metadata.get("status") in ACTIVE:
                metadata.update(status="interrupted", error="توقف الخادم أثناء التصدير / Server stopped during export")
                _write_json(folder / "job.json", metadata)
            if metadata.get("status") == "interrupted":
                self._cleanup(folder, retry=True)

    def _folders(self):
        return [path for path in self.root.iterdir() if _ID.fullmatch(path.name)
                and path.is_dir() and not path.is_symlink()]

    def _folder(self, job_id: str, owner: int, admin: bool = False) -> Path:
        folder = self.root / str(job_id)
        metadata_path = folder / "job.json"
        if (not _ID.fullmatch(str(job_id)) or folder.is_symlink() or not folder.is_dir()
                or metadata_path.is_symlink() or not metadata_path.is_file()):
            raise ReportJobError("التصدير غير موجود / Export not found", 404)
        metadata = _read_json(metadata_path)
        if not admin and metadata.get("owner_user_id") != owner:
            raise ReportJobError("التصدير غير موجود / Export not found", 404)
        return folder

    @staticmethod
    def _cleanup(folder: Path, retry: bool = False) -> None:
        try:
            workspace = folder / "work"
            if workspace.is_dir() and not workspace.is_symlink():
                shutil.rmtree(workspace)
            for name in ("report.csv", "report.xlsx", "result.json"):
                (folder / name).unlink(missing_ok=True)
        except OSError:
            if not retry:
                raise

    def _refresh(self):
        for folder in self._folders():
            if _read_json(folder / "job.json").get("status") == "interrupted":
                self._cleanup(folder, retry=True)
        for job_id, process in list(self._processes.items()):
            if process.poll() is not None:
                self._complete(job_id, process)

    def _public(self, folder: Path) -> dict:
        metadata = _read_json(folder / "job.json")
        job = {key: metadata.get(key) for key in ("id", "status", "created_at", "config")}
        job["progress"] = _read_json(folder / "progress.json") or {
            "stage": "queued", "message": "جارٍ بدء التصدير / Starting export", "percent": 0}
        if metadata.get("error"):
            job["error"] = metadata["error"]
        if job["status"] == "passed":
            job["result"] = _read_json(folder / "result.json")
        path = folder / ("report." + metadata.get("config", {}).get("fmt", "csv"))
        job["downloads"] = {"file": job["status"] == "passed" and path.is_file() and not path.is_symlink()}
        return job

    def list(self, owner: int, admin: bool = False) -> list[dict]:
        with self._lock:
            self._refresh()
            jobs = [self._public(folder) for folder in self._folders()
                    if admin or _read_json(folder / "job.json").get("owner_user_id") == owner]
            return sorted(jobs, key=lambda job: job.get("created_at") or "", reverse=True)

    def get(self, job_id: str, owner: int, admin: bool = False) -> dict:
        with self._lock:
            self._refresh()
            return self._public(self._folder(job_id, owner, admin))

    def _environment(self, folder: Path) -> dict:
        env = {key: value for key, value in os.environ.items() if not key.startswith("HADER_")}
        # Do not leave connection credentials in public job files or logs.
        from sqlalchemy.engine import make_url
        from .timekeeping import zone_name
        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite":
            if not url.database or url.database == ":memory:":
                raise ReportJobError("التصدير الخلفي يتطلب قاعدة بيانات محفوظة / Background export requires a persistent database", 503)
            if not url.database.startswith("file:"):
                url = url.set(database=str(Path(url.database).resolve()))
        env.update({"HADER_REPORT_DATABASE_URL": url.render_as_string(hide_password=False),
                    "HADER_REPORT_TIMEZONE": zone_name(),
                    "HADER_REPORT_MAX_BYTES": str(MAX_FILE_BYTES), "HADER_DATA_DIR": str(folder / "work"),
                    "HADER_CONFIG": str(folder / "work" / "report.ini"),
                    "HADER_SECRET_KEY": secrets.token_hex(32), "HADER_HOST": "127.0.0.1",
                    "HADER_WEB_PORT": "0", "HADER_PORTAL_PORT": "0", "HADER_ADMS_PORTS": ""})
        return env

    def start(self, options: dict, owner: int, on_accept=None) -> dict:
        config = validate_options(options)
        with self._lock:
            self._refresh()
            if self._closed:
                raise ReportJobError("الخادم يتوقف / Server is stopping")
            if len(self._processes) >= MAX_ACTIVE:
                raise ReportJobError("يوجد تصديران يعملان؛ انتظر أو أوقف أحدهما / Two exports are active; wait or stop one")
            if len(self._folders()) >= MAX_SAVED:
                raise ReportJobError("احذف تصديراً قديماً؛ الحد عشرة ملفات محفوظة / Delete an old export; ten saved exports allowed")
            if not self.worker_path.is_file():
                raise ReportJobError("ملف تشغيل التصدير غير موجود / Export worker is missing", 503)
            job_id = uuid.uuid4().hex
            folder = self.root / job_id
            folder.mkdir(mode=0o700)
            metadata = {"id": job_id, "owner_user_id": owner, "status": "queued",
                        "created_at": datetime.now(timezone.utc).isoformat(), "config": config}
            try:
                env = self._environment(folder)
                (folder / "work").mkdir(mode=0o700)
                (folder / "work" / "report.ini").write_text("[server]\n", encoding="utf-8")
                _write_json(folder / "config.json", config)
                _write_json(folder / "job.json", metadata)
                if on_accept:
                    on_accept(job_id)
                with (folder / "worker.log").open("wb") as log:
                    try:
                        process = subprocess.Popen([sys.executable, str(self.worker_path), "--job-dir", str(folder), "--supervised"],
                            cwd=self.worker_path.parent.parent, env=env, stdin=subprocess.PIPE, stdout=log,
                            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    except OSError as exc:
                        raise ReportJobError("تعذّر بدء التصدير؛ تحقق من Python / Could not start export; check Python", 503) from exc
            except Exception:
                shutil.rmtree(folder)
                raise
            self._processes[job_id] = process
            metadata["status"] = "running"
            _write_json(folder / "job.json", metadata)
            timer = self._timers[job_id] = threading.Timer(TIMEOUT_SECONDS, self._expire, args=(job_id,))
            timer.daemon = True
            timer.start()
            threading.Thread(target=self._wait, args=(job_id, process), name="report-export", daemon=True).start()
            return self._public(folder)

    def _wait(self, job_id: str, process: subprocess.Popen) -> None:
        process.wait()
        with self._lock:
            if self._processes.get(job_id) is process:
                self._complete(job_id, process)

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _complete(self, job_id: str, process: subprocess.Popen, status: str | None = None, error: str | None = None) -> None:
        if self._processes.get(job_id) is not process:
            return
        folder = self.root / job_id
        metadata = _read_json(folder / "job.json")
        result = _read_json(folder / "result.json")
        target = folder / ("report." + metadata["config"]["fmt"])
        if status is None:
            status = "passed" if process.returncode == 0 and result.get("status") == "passed" and target.is_file() else "failed"
        metadata["status"] = status
        if error or status == "failed":
            metadata["error"] = error or str(result.get("error") or "تعذّر إنشاء التقرير / Report export failed")[:500]
        _write_json(folder / "job.json", metadata)
        self._processes.pop(job_id, None)
        timer = self._timers.pop(job_id, None)
        if timer:
            timer.cancel()
        if process.stdin:
            process.stdin.close()
        if status != "passed":
            self._cleanup(folder, retry=True)
            _write_json(folder / "progress.json", {"stage": status, "percent": 0,
                        "message": metadata.get("error") or "أُوقف التصدير / Export stopped"})

    def _expire(self, job_id: str) -> None:
        with self._lock:
            process = self._processes.get(job_id)
            if process:
                if process.poll() is None:
                    self._stop(process)
                    self._complete(job_id, process, "failed", "انتهت مهلة التصدير (20 دقيقة) / Export exceeded 20 minutes")
                else:
                    self._complete(job_id, process)

    def cancel(self, job_id: str, owner: int, admin: bool = False) -> dict:
        with self._lock:
            self._refresh()
            folder = self._folder(job_id, owner, admin)
            process = self._processes.get(job_id)
            if process:
                self._stop(process)
                self._complete(job_id, process, "cancelled")
            return self._public(folder)

    def delete(self, job_id: str, owner: int, admin: bool = False) -> None:
        with self._lock:
            self._refresh()
            folder = self._folder(job_id, owner, admin)
            if job_id in self._processes:
                raise ReportJobError("أوقف التصدير قبل حذفه / Stop export before deleting it")
            shutil.rmtree(folder)

    def download_path(self, job_id: str, owner: int, admin: bool = False) -> Path:
        with self._lock:
            self._refresh()
            folder = self._folder(job_id, owner, admin)
            metadata = _read_json(folder / "job.json")
            path = folder / ("report." + metadata.get("config", {}).get("fmt", "csv"))
            if metadata.get("status") != "passed" or not path.is_file() or path.is_symlink():
                raise ReportJobError("الملف غير جاهز / File is not ready", 404)
            return path

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            for job_id, process in list(self._processes.items()):
                self._stop(process)
                self._complete(job_id, process, "cancelled")


_MANAGERS: dict[Path, ReportJobManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_manager() -> ReportJobManager:
    from .config import settings
    root = settings.data_dir / "report_jobs"
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(root)
        if manager is None or manager._closed:
            manager = _MANAGERS[root] = ReportJobManager(root, settings.database_url)
        return manager


def shutdown_managers() -> None:
    with _MANAGERS_LOCK:
        for manager in _MANAGERS.values():
            manager.shutdown()
        _MANAGERS.clear()
