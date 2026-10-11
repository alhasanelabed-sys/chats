"""Export a consistent, read-only live-data snapshot without starting servers."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path


def prepare(job: Path) -> None:
    database_url = os.environ.get("HADER_REPORT_DATABASE_URL", "")
    maximum = os.environ.get("HADER_REPORT_MAX_BYTES", str(1024 * 1024 * 1024))
    time_zone = os.environ.get("HADER_REPORT_TIMEZONE", "")
    for name in tuple(os.environ):
        if name.startswith("HADER_"):
            del os.environ[name]
    workspace = job / "work"
    workspace.mkdir(mode=0o700, exist_ok=True)
    ini = workspace / "report.ini"
    ini.write_text("[server]\n", encoding="utf-8")
    os.environ.update({"HADER_DATA_DIR": str(workspace), "HADER_CONFIG": str(ini),
        # hader.db is imported for model declarations only. Its unused engine
        # must never point at the actual attendance database.
        "HADER_DATABASE_URL": "sqlite:///:memory:", "HADER_SECRET_KEY": secrets.token_hex(32),
        "HADER_TIMEZONE": time_zone,
        "HADER_HOST": "127.0.0.1", "HADER_WEB_PORT": "0", "HADER_PORTAL_PORT": "0", "HADER_ADMS_PORTS": ""})
    os.environ["HADER_REPORT_DATABASE_URL"] = database_url
    os.environ["HADER_REPORT_MAX_BYTES"] = maximum
    tempfile.tempdir = str(workspace)


def read_engine(database_url: str):
    """Use enforced read-only connections and one repeatable-read transaction."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("A persistent SQLite database is required")
        database = url.database
        if database.startswith("file:"):
            # SQLAlchemy SQLite URI configurations may contain separate query
            # options. Keep the selected database while enforcing read-only.
            database = database.split("?", 1)[0]
            uri = database + "?mode=ro"
        else:
            uri = Path(database).resolve().as_uri() + "?mode=ro"

        def connect():
            connection = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection

        return create_engine("sqlite://", creator=connect), backend
    if backend not in {"postgresql", "mysql", "mariadb"}:
        raise ValueError("Unsupported database for consistent read-only export")
    return create_engine(url, isolation_level="REPEATABLE READ"), backend


class FileLimitError(ValueError):
    pass


class BoundedFile:
    """Enforce the final archive limit during ZIP/CSV writes, including seeks."""
    def __init__(self, target, maximum: int):
        self.target, self.maximum = target, maximum

    def write(self, data):
        if self.target.tell() + len(data) > self.maximum:
            raise FileLimitError("Export exceeds the file size limit")
        return self.target.write(data)

    def __getattr__(self, name):
        return getattr(self.target, name)


def run(job: Path) -> int:
    prepare(job)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hader.report_jobs import MAX_FILE_BYTES, _write_json, validate_options
    result = {"status": "failed"}
    started = time.perf_counter()
    engine = None
    workspace = job / "work"
    try:
        config_path = job / "config.json"
        if config_path.stat().st_size > 65536:
            raise ValueError("Oversized export configuration")
        config = validate_options(json.loads(config_path.read_text(encoding="utf-8")))
        maximum = min(MAX_FILE_BYTES, max(1, int(os.environ["HADER_REPORT_MAX_BYTES"])))
        from sqlalchemy.orm import Session
        from hader import reports, store
        engine, backend = read_engine(os.environ["HADER_REPORT_DATABASE_URL"])

        def progress(stage, percent, message):
            _write_json(job / "progress.json", {"stage": stage, "percent": percent, "message": message})

        progress("snapshot", 5, "فتح نسخة قراءة متسقة / Opening a consistent read snapshot")
        with engine.connect() as connection:
            if backend == "sqlite":
                # sqlite3 legacy transaction mode does not BEGIN on SELECT.
                # An explicit BEGIN makes every lazy query see one snapshot.
                connection.exec_driver_sql("BEGIN")
            else:
                connection.begin()
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            try:
                with Session(bind=connection, autoflush=False) as db:
                    parse_ids = lambda value: [int(item) for item in value.split(",")] if value else None
                    report = reports.build(db, config["key"], date.fromisoformat(config["start"]),
                        date.fromisoformat(config["end"]), employee_ids=parse_ids(config["employee_ids"]),
                        department_ids=parse_ids(config["department_ids"]), device_sn=config["device"] or None,
                        lang=config["lang"], q=config["q"], status=config["status"], limit=None, stream=True)
                    rows = report["rows"]
                    count = 0

                    def counted_rows():
                        nonlocal count
                        for row in rows:
                            count += 1
                            if count % 1000 == 0:
                                progress("export", 50, f"تصدير {count:,} صف / Exported {count:,} rows")
                            yield row

                    report["rows"] = counted_rows()
                    progress("export", 20, "تصدير جميع صفوف التقرير / Exporting every report row")
                    partial = workspace / ("report.partial." + config["fmt"])
                    with partial.open("w+b") as output:
                        bounded = BoundedFile(output, maximum)
                        if config["fmt"] == "csv":
                            for chunk in reports.iter_csv(report):
                                bounded.write(chunk)
                        else:
                            company = store.get(db, "company.name_ar" if config["lang"] == "ar" else "company.name")
                            reports.to_xlsx(report, company=company, rtl=config["lang"] == "ar", output=bounded)
                        output.flush()
                        os.fsync(output.fileno())
                    target = job / ("report." + config["fmt"])
                    os.replace(partial, target)
                    result.update(status="passed", rows=count, size_bytes=target.stat().st_size,
                                  filename=f"{config['key']}_{config['start']}_{config['end']}.{config['fmt']}",
                                  consistent_snapshot=True, read_only_database=True)
            finally:
                connection.rollback()
        progress("complete", 100, "التقرير جاهز للتنزيل / Report ready to download")
    except Exception as exc:
        for name in ("report.csv", "report.xlsx"):
            (job / name).unlink(missing_ok=True)
        # Do not publish SQL statements, database credentials or private paths.
        error = ("حجم التقرير يتجاوز الحد (1 GiB) / Report exceeds the file size limit"
                 if isinstance(exc, FileLimitError) else f"تعذّر إنشاء التقرير / Report export failed ({type(exc).__name__})")
        result.update(status="failed", error=error)
        _write_json(job / "progress.json", {"stage": "failed", "percent": 0, "message": error})
    finally:
        if engine is not None:
            engine.dispose()
        # Includes openpyxl's temporary XML files; no partial export survives.
        shutil.rmtree(workspace, ignore_errors=True)
    result["seconds"] = round(time.perf_counter() - started, 6)
    _write_json(job / "result.json", result)
    return 0 if result["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true")
    args = parser.parse_args()
    job = args.job_dir.resolve()
    if not job.is_dir():
        parser.error("--job-dir must exist")
    if args.supervised:
        descriptor = sys.stdin.fileno()

        def watch_owner():
            try:
                while os.read(descriptor, 1):
                    pass
            except OSError:
                pass
            os._exit(3)

        threading.Thread(target=watch_owner, name="report-owner", daemon=True).start()
    return run(job)


if __name__ == "__main__":
    raise SystemExit(main())
