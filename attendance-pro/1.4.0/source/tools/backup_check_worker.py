"""Recover a snapshot into a private SQLite database; never touch the live DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rehearse(source: Path, scratch: Path) -> dict:
    from hader.backup import BackupError, validate_snapshot
    metadata = validate_snapshot(source)
    database = scratch / "recovered.db"
    if metadata["format"] == "legacy_db":
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(database)) as dst:
            src.backup(dst)
    else:
        with zipfile.ZipFile(source) as archive, archive.open("hader.db") as src, database.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    with closing(sqlite3.connect(database)) as recovered:
        if recovered.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError("recovered database failed integrity check")
        names = {row[0] for row in recovered.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for table in ("employee", "device", "transaction", "audit_log"):
            if table in names:
                counts[table] = recovered.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
        # A closed, newly reopened recovered database proves the artifact can
        # operate independently; no application restore marker is created.
    with closing(sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True)) as reopened:
        if reopened.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise BackupError("recovered database could not be reopened")
    return {"manifest_verified": metadata["format"] == "snapshot_zip", "integrity": "ok", "counts": counts}


def run(job: Path) -> int:
    started = time.monotonic()
    config = json.loads((job / "config.json").read_text(encoding="utf-8"))
    source = Path(config["source"])
    temporary_copy = None
    result = {"status": "failed", "restore_apply_performed": False, "offsite_copy_verified": False}
    try:
        with tempfile.TemporaryDirectory(prefix="rehearsal_", dir=job) as sandbox:
            # Import settings only after establishing isolated data/config paths.
            # Inherited credentials and production data are never needed.
            for key in list(os.environ):
                if key.startswith("HADER_"):
                    os.environ.pop(key)
            os.environ.update(HADER_DATA_DIR=sandbox, HADER_CONFIG=str(Path(sandbox) / "unused.ini"),
                              HADER_DATABASE_URL="sqlite:///" + (Path(sandbox) / "unused.db").as_posix(),
                              HADER_SECRET_KEY=os.urandom(32).hex(), HADER_TIMEZONE="",
                              HADER_STORAGE_SYNCHRONOUS="NORMAL", HADER_ADMS_PORTS="", HADER_PORTAL_PORT="0")
            result.update(_rehearse(source, Path(sandbox)))
            if config.get("offsite"):
                folder = Path(config["offsite"])
                folder.mkdir(parents=True, exist_ok=True)
                name = str(config["archive_name"])
                if Path(name).name != name or name in {".", ".."}:
                    raise ValueError("invalid backup filename")
                target = folder / name
                temporary_copy = folder / ("." + name + "." + job.name + ".tmp" + source.suffix.lower())
                with source.open("rb") as src, temporary_copy.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                if _sha(source) != _sha(temporary_copy):
                    raise ValueError("external copy checksum mismatch")
                # Validate the external bytes and rehearse from them, before
                # exposing the finished filename to backup consumers.
                result.update(_rehearse(temporary_copy, Path(sandbox)))
                os.replace(temporary_copy, target)
                result["offsite_copy_verified"] = True
            result["status"] = "passed"
    except Exception:
        # Paths can contain share credentials. Keep public state path-free.
        result["error"] = "تعذر التحقق من سلامة النسخة أو الوصول للمجلد الخارجي / Backup integrity or external folder check failed"
    finally:
        if temporary_copy is not None:
            try:
                temporary_copy.unlink(missing_ok=True)
            except OSError:
                pass
    result["seconds"] = round(time.monotonic() - started, 3)
    result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(job / "result.json", result)
    return 0 if result["status"] == "passed" else 1


def _supervise():
    try:
        while os.read(sys.stdin.fileno(), 1):
            pass
    except (OSError, ValueError):
        pass
    os._exit(3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--supervised", action="store_true")
    options = parser.parse_args()
    if options.supervised:
        threading.Thread(target=_supervise, daemon=True).start()
    return run(Path(options.job_dir).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
