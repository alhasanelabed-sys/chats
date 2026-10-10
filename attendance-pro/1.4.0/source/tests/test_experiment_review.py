"""Independent checks for experiment restart and process supervision boundaries."""
import json
import subprocess
import sys
import time

from hader.experiments import ExperimentManager


def test_restarting_manager_removes_interrupted_disposable_data(tmp_path):
    root = tmp_path / "experiments"
    folder = root / ("a" * 32)
    disposable = folder / "experiment_interrupted"
    disposable.mkdir(parents=True)
    (disposable / "hader.db").write_bytes(b"disposable synthetic database")
    (disposable / "initial_admin_password.txt").write_text("disposable credential")
    (folder / "job.json").write_text(json.dumps({"id": folder.name, "status": "running"}))
    for name in ("transactions.csv", "preview.json", "result.json"):
        (folder / name).write_text("partial artifact")
    outside = tmp_path / "production.db"
    outside.write_bytes(b"existing attendance data")
    manager = ExperimentManager(root)
    try:
        job = manager.get(folder.name)
        assert job["status"] == "interrupted"
        assert job["downloads"] == {"json": False, "csv": False}
        assert not disposable.exists(), "Restart retained an interrupted synthetic DB and credentials"
        assert all(not (folder / name).exists() for name in ("transactions.csv", "preview.json", "result.json"))
        assert outside.read_bytes() == b"existing attendance data"
    finally:
        manager.shutdown()


def test_timeout_stops_and_cleans_worker_without_ui_polling(tmp_path, monkeypatch):
    from hader import experiments
    worker = tmp_path / "worker.py"
    worker.write_text("""
import sys, time
from pathlib import Path
job = Path(sys.argv[sys.argv.index('--job-dir') + 1])
temporary = job / 'experiment_timeout'
temporary.mkdir()
(temporary / 'hader.db').write_bytes(b'disposable data')
time.sleep(60)
""")
    monkeypatch.setattr(experiments, "TIMEOUT_SECONDS", 0.2)
    manager = ExperimentManager(tmp_path / "experiments", worker_path=worker)
    try:
        job = manager.start({"employees": 1, "devices": 1, "days": 1, "repeat": 1,
                             "export_csv": False, "ingestion": False})
        process = manager._processes[job["id"]][0]
        deadline = time.monotonic() + 5
        # Read only process state. No manager.get/list call may trigger _refresh.
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process.poll() is not None, "Timeout depended on administrator polling"
        with manager._lock:
            assert job["id"] not in manager._processes
            folder = manager.root / job["id"]
            metadata = json.loads((folder / "job.json").read_text())
            assert metadata["status"] == "failed"
            assert not any(path.is_dir() for path in folder.iterdir())
    finally:
        manager.shutdown()


def test_restart_retries_a_brief_windows_file_lock(tmp_path, monkeypatch):
    root = tmp_path / "experiments"
    folder = root / ("b" * 32)
    temporary = folder / "experiment_interrupted"
    temporary.mkdir(parents=True)
    (temporary / "hader.db").write_bytes(b"disposable synthetic database")
    (folder / "job.json").write_text(json.dumps({"id": folder.name, "status": "running"}))
    original = ExperimentManager._remove_working_files
    attempts = []

    def locked_once(job_folder):
        attempts.append(job_folder)
        if len(attempts) == 1:
            raise PermissionError("SQLite handle is still closing")
        return original(job_folder)

    monkeypatch.setattr(ExperimentManager, "_remove_working_files", staticmethod(locked_once))
    manager = ExperimentManager(root)
    try:
        assert temporary.exists()
        assert json.loads((folder / "job.json").read_text())["status"] == "interrupted"
        assert manager.get(folder.name)["status"] == "interrupted"
        assert len(attempts) == 2 and not temporary.exists()
    finally:
        manager.shutdown()


def test_owner_pipe_death_stops_active_worker_and_restart_cleans_database(tmp_path):
    root = tmp_path / "experiments"
    folder = root / ("c" * 32)
    folder.mkdir(parents=True)
    options = {"employees": 50000, "devices": 20, "days": 20, "repeat": 1,
               "export_csv": False, "ingestion": False}
    (folder / "config.json").write_text(json.dumps(options))
    (folder / "job.json").write_text(json.dumps({"id": folder.name, "status": "running", "config": options}))
    from pathlib import Path
    worker = Path(__file__).resolve().parents[1] / "tools" / "experiment_worker.py"
    with subprocess.Popen([sys.executable, str(worker), "--job-dir", str(folder), "--supervised"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            deadline = time.monotonic() + 10
            while not list(folder.glob("experiment_*")):
                assert process.poll() is None
                assert time.monotonic() < deadline, "Worker did not create its disposable workspace"
                time.sleep(0.02)
            process.stdin.close()
            assert process.wait(timeout=10) == 3
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    manager = ExperimentManager(root)
    try:
        assert manager.get(folder.name)["status"] == "interrupted"
        assert not any(path.is_dir() for path in folder.iterdir())
        assert not (folder / "transactions.csv").exists()
    finally:
        manager.shutdown()
