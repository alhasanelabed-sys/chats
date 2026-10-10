"""Exercise the UI experiment subprocess against isolated synthetic data."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tools" / "experiment_worker.py"


def launch_worker(job: Path, production: Path, options: dict,
                  script: str | None = None) -> subprocess.CompletedProcess:
    job.mkdir()
    (job / "config.json").write_text(json.dumps(options), encoding="utf-8")
    environment = os.environ.copy()
    environment.update({
        "HADER_DATA_DIR": str(production),
        "HADER_DATABASE_URL": f"sqlite:///{(production / 'production.db').as_posix()}",
        "HADER_CONFIG": str(production / "production.ini"),
        "HADER_SECRET_KEY": "inherited-secret-must-never-be-published",
        "HADER_HOST": "203.0.113.7",
        "HADER_ADMS_PORTS": "80,90",
        "HADER_PORTAL_PORT": "8091",
    })
    command = ([sys.executable, "-c", script, str(job)] if script is not None
               else [sys.executable, str(WORKER), "--job-dir", str(job)])
    return subprocess.run(
        command,
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=90,
    )


def production_folder(folder: Path) -> dict[str, bytes]:
    folder.mkdir()
    with sqlite3.connect(folder / "production.db") as db:
        db.execute("CREATE TABLE important_data (value TEXT)")
        db.execute("INSERT INTO important_data VALUES ('preserve existing production data')")
    (folder / "production.ini").write_text("[server]\n", encoding="utf-8")
    (folder / "photo.jpg").write_bytes(b"existing-private-photo")
    return {path.name: path.read_bytes() for path in folder.iterdir()}


def test_worker_retains_validated_csv_and_preview_without_touching_production(tmp_path):
    production = tmp_path / "production"
    before = production_folder(production)
    job = tmp_path / "job"
    completed = launch_worker(job, production, {
        "employees": 24, "devices": 3, "days": 3, "repeat": 1,
        "export_csv": True, "ingestion": True, "keep_write_off": True,
    })
    assert completed.returncode == 0, completed.stderr
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["employees"] == 24 and result["devices"] == 3
    assert result["days"] == 3 and result["punches"] == 144
    assert result["physical_devices_verified"] is False
    assert result["isolated_temporary_database"] is True
    assert result["tcp_write_back"] is False
    assert result["export"]["data_rows"] == 144
    assert result["export"]["retained_csv"] is True
    assert result["export"]["temporary_csv_removed_on_exit"] is False
    assert result["ingestion"]["http_200_replies"] == 3
    assert result["ingestion"]["punches_before"] == result["ingestion"]["punches_after"] == 144
    with (job / "transactions.csv").open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 144
    late_punches = [row for row in rows if row["ID"] == "20" and row["State"] == "Check-In"]
    assert len(late_punches) == 3
    assert all(row["Time"] == "08:15:00" for row in late_punches)
    preview = json.loads((job / "preview.json").read_text(encoding="utf-8"))
    assert len(preview["employees"]) == 20
    assert all(set(row) == {"emp_code", "name"} for row in preview["employees"])
    assert len(preview["daily"]) == 20
    assert all(row["status"] in {"present", "late"} for row in preview["daily"])
    progress = json.loads((job / "progress.json").read_text(encoding="utf-8"))
    assert progress["stage"] == "complete" and progress["percent"] == 100
    assert {path.name: path.read_bytes() for path in production.iterdir()} == before
    assert {path.name for path in job.iterdir()} == {
        "config.json", "progress.json", "preview.json", "result.json", "transactions.csv",
    }
    assert not any("inherited-secret" in path.read_text(encoding="utf-8")
                   for path in job.iterdir() if path.suffix == ".json")


def test_worker_optional_steps_do_not_leave_csv_or_database(tmp_path):
    production = tmp_path / "production"
    before = production_folder(production)
    job = tmp_path / "job"
    completed = launch_worker(job, production, {
        "employees": 2, "devices": 1, "days": 1, "repeat": 1,
        "export_csv": False, "ingestion": False, "keep_write_off": False,
    })
    assert completed.returncode == 0, completed.stderr
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed" and result["punches"] == 4
    assert "export" not in result and "ingestion" not in result
    assert result["tcp_write_back"] is False
    assert not (job / "transactions.csv").exists()
    assert not any(path.is_dir() or path.suffix in {".db", ".sqlite", ".ini"} for path in job.iterdir())
    assert {path.name: path.read_bytes() for path in production.iterdir()} == before


def test_worker_invalid_options_produce_failure_result_and_nonzero_exit(tmp_path):
    production = tmp_path / "production"
    before = production_folder(production)
    job = tmp_path / "job"
    completed = launch_worker(job, production, {"employees": -1})
    assert completed.returncode != 0
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed" and result["error"]
    progress = json.loads((job / "progress.json").read_text(encoding="utf-8"))
    assert progress["stage"] == "failed"
    assert not any(path.is_dir() for path in job.iterdir())
    assert {path.name: path.read_bytes() for path in production.iterdir()} == before


def test_worker_failed_validation_cleans_export_and_redacts_private_paths(tmp_path):
    production = tmp_path / "production"
    before = production_folder(production)
    job = tmp_path / "job"
    # Inject only at the genuine benchmark boundary; data seeding, report
    # calculations, export and the worker's failure cleanup remain real.
    script = """
import os, sys
from pathlib import Path
from tools import benchmark_scale as benchmark
from tools.experiment_worker import run
def fail_ingestion(*args):
    raise RuntimeError('database ' + os.environ['HADER_DATA_DIR'] + ' secret ' + os.environ['HADER_SECRET_KEY'])
benchmark.benchmark_ingestion = fail_ingestion
raise SystemExit(run(Path(sys.argv[1])))
"""
    completed = launch_worker(job, production, {
        "employees": 2, "devices": 1, "days": 1, "repeat": 1,
        "export_csv": True, "ingestion": True, "keep_write_off": True,
    }, script=script)
    assert completed.returncode == 1
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["error"] == "RuntimeError: database [private] secret [private]"
    assert result["export"]["retained_csv"] is False
    assert not (job / "transactions.csv").exists()
    assert not any(path.is_dir() for path in job.iterdir())
    assert {path.name: path.read_bytes() for path in production.iterdir()} == before


def test_supervised_worker_completes_while_parent_keeps_pipe_open(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "config.json").write_text(json.dumps({
        "employees": 2, "devices": 1, "days": 1, "repeat": 1,
        "export_csv": False, "ingestion": False,
    }), encoding="utf-8")
    with subprocess.Popen(
        [sys.executable, str(WORKER), "--job-dir", str(job), "--supervised"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as child:
        try:
            assert child.wait(timeout=30) == 0, child.stderr.read().decode()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    assert json.loads((job / "result.json").read_text(encoding="utf-8"))["status"] == "passed"


def test_supervised_worker_stops_when_parent_pipe_closes(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "config.json").write_text("{}", encoding="utf-8")
    with subprocess.Popen(
        [sys.executable, str(WORKER), "--job-dir", str(job), "--supervised"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as child:
        try:
            child.stdin.close()
            assert child.wait(timeout=30) == 3
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
