"""Run one UI-requested synthetic experiment in an isolated child process.

The parent creates ``config.json`` in a trusted private job directory. This
worker retains only reports and a small preview, never its database, password
or photos. It does not start an HTTP listener or contact physical devices.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def write_json(path: Path, value: dict) -> None:
    """Publish a complete UTF-8 document; readers never see a partial write."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        # The file is closed before replacement, including on Windows.
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def progress(job: Path, stage: str, percent: int, message: str) -> None:
    write_json(job / "progress.json", {
        "stage": stage, "percent": percent, "message": message,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    })


def isolate(folder: Path) -> None:
    # This child must not inherit the operator's database, session secret or
    # listening ports. Apply every override before importing any hader module.
    for key in tuple(os.environ):
        if key.startswith("HADER_"):
            del os.environ[key]
    ini = folder / "experiment.ini"
    ini.write_text("[server]\n", encoding="utf-8")
    os.environ.update({
        "HADER_DATA_DIR": str(folder),
        "HADER_DATABASE_URL": f"sqlite:///{(folder / 'hader.db').as_posix()}",
        "HADER_CONFIG": str(ini),
        "HADER_SECRET_KEY": secrets.token_hex(32),
        "HADER_HOST": "127.0.0.1",
        "HADER_WEB_PORT": "0",
        "HADER_ADMS_PORTS": "",
        "HADER_PORTAL_PORT": "0",
    })


def safe_error(exc: Exception, job: Path, folder: Path | None) -> str:
    """Keep useful diagnostics without publishing private paths or secrets."""
    message = str(exc)
    private_values = (str(job), str(folder) if folder else "", os.environ.get("HADER_SECRET_KEY", ""))
    for private in sorted(private_values, key=len, reverse=True):
        if private:
            message = message.replace(private, "[private]")
    # Errors are plain text in result.json; controls and unbounded exception
    # payloads should not reach the experiment page.
    message = " ".join(message.split())[:500]
    return f"{type(exc).__name__}: {message or 'Experiment failed'}"


def run(job: Path) -> int:
    started = time.perf_counter()
    result = {
        "status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "cpu_count": os.cpu_count()},
        "isolated_temporary_database": True, "physical_devices_verified": False,
        "tcp_write_back": False,
    }
    folder = None
    try:
        progress(job, "seed", 5, "Preparing an isolated synthetic database")
        with tempfile.TemporaryDirectory(prefix="experiment_", dir=job) as temporary:
            folder = Path(temporary)
            isolate(folder)
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            # The validation module has no application/configuration imports.
            # Even so, it is imported only after establishing isolation.
            from hader.experiment_config import validate_options
            from tools import benchmark_scale as benchmark
            from sqlalchemy import func, select
            from hader import models as m, reports, store
            from hader.db import engine, session_scope
            from hader.version import VERSION

            try:
                config_file = job / "config.json"
                if config_file.stat().st_size > 65536:
                    raise ValueError("Experiment configuration is too large")
                options = validate_options(json.loads(config_file.read_text(encoding="utf-8")))
                args = argparse.Namespace(**options)
                result.update({key: options[key] for key in ("employees", "devices", "days", "repeat")})
                result.update({"version": VERSION, "punches": args.employees * args.days * 2,
                               "options": options})
                end = date.today() - timedelta(days=2)
                start = end - timedelta(days=args.days - 1)
                result["date_range"] = {"start": start.isoformat(), "end": end.isoformat()}
                before = time.perf_counter()
                ids = benchmark.seed(args, start, end)
                result["seed_seconds"] = round(time.perf_counter() - before, 6)
                with session_scope() as db:
                    for model, expected in ((m.Employee, args.employees), (m.Device, args.devices),
                                            (m.Transaction, result["punches"])):
                        actual = db.scalar(select(func.count()).select_from(model))
                        benchmark.require(actual == expected, f"{model.__name__} seed count is incorrect")
                    for key in ("tcp.write_back", "discovery.enabled", "alerts.enabled", "adms.auto_add"):
                        benchmark.require(store.get(db, key) is False, f"{key} must remain disabled")
                    unsafe_devices = db.scalar(select(func.count()).select_from(m.Device).where(
                        (m.Device.tcp_poll == True) | (m.Device.ip != "")))  # noqa: E712
                    benchmark.require(unsafe_devices == 0, "Synthetic devices must not have network addresses")
                    preview = {
                        "employees": [{"emp_code": employee.emp_code, "name": employee.full_name}
                                      for employee in db.scalars(select(m.Employee).order_by(m.Employee.id).limit(20))],
                        "daily": reports.build(db, "daily", start, end, limit=20, lang="en")["rows"],
                    }
                write_json(job / "preview.json", preview)
                progress(job, "reports", 35, "Validating attendance calculations and report pages")
                result["reports"] = benchmark.benchmark_reports(args, start, end, ids)
                if args.export_csv:
                    progress(job, "export", 55, "Exporting and validating all synthetic punches")
                    result["export"] = benchmark.benchmark_export(args, folder, start, end)
                    os.replace(folder / "transactions.csv", job / "transactions.csv")
                    result["export"].update({"retained_csv": True, "filename": "transactions.csv",
                                             "temporary_csv_removed_on_exit": False})
                if args.ingestion:
                    progress(job, "ingestion", 80, "Simulating concurrent uploads without physical devices")
                    result["ingestion"] = benchmark.benchmark_ingestion(args, start)
                result["status"] = "passed"
            finally:
                # Dispose connections before TemporaryDirectory removes the
                # SQLite database and its sidecars on Windows.
                engine.dispose()
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = safe_error(exc, job, folder)
        # A partial export is not a validated downloadable artifact.
        (job / "transactions.csv").unlink(missing_ok=True)
        if "export" in result:
            result["export"].update({"retained_csv": False, "temporary_csv_removed_on_exit": True})
            result["export"].pop("filename", None)
    result["total_seconds"] = round(time.perf_counter() - started, 6)
    write_json(job / "result.json", result)
    if result["status"] == "passed":
        progress(job, "complete", 100, "Experiment completed and temporary database removed")
        return 0
    progress(job, "failed", 100, result["error"])
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true",
                        help="stop if the owning server closes its stdin pipe")
    args = parser.parse_args()
    job = args.job_dir.resolve()
    if not job.is_dir():
        parser.error("--job-dir must be an existing experiment directory")
    if args.supervised:
        descriptor = sys.stdin.fileno()

        def watch_owner() -> None:
            # The parent owns the write end. EOF means it exited or cancelled
            # this job; never inspect/reuse a PID, especially on Windows. Use
            # os.read rather than a buffered Python reader so a daemon waiting
            # on stdin cannot hold an I/O lock during normal interpreter exit.
            try:
                while os.read(descriptor, 1):
                    pass
            except OSError:
                pass
            os._exit(3)

        threading.Thread(target=watch_owner, name="experiment-owner", daemon=True).start()
    return run(job)


if __name__ == "__main__":
    raise SystemExit(main())
