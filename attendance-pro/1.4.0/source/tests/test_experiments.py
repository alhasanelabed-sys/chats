"""Exercise the administrator's isolated experiment workflow through its API."""
from datetime import datetime
import csv
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hader import models as m, store
from hader.app import app
from hader.db import session_scope


BASE = "/api/experiments"
SMALL = {"employees": 20, "devices": 2, "days": 2, "repeat": 1,
         "export_csv": True, "ingestion": True, "keep_write_off": True}


@pytest.fixture()
def experiment_manager(tmp_path, monkeypatch):
    from hader.experiments import ExperimentManager
    from hader.api import experiments

    manager = ExperimentManager(tmp_path / "experiments")
    monkeypatch.setattr(experiments, "get_manager", lambda: manager)
    try:
        yield manager
    finally:
        manager.shutdown()


def _main_state():
    with session_scope() as db:
        return {"employees": db.scalar(select(func.count()).select_from(m.Employee)),
                "devices": db.scalar(select(func.count()).select_from(m.Device)),
                "punches": db.scalar(select(func.count()).select_from(m.Transaction)),
                "write_back": store.get(db, "tcp.write_back")}


def _wait(client, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{BASE}/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    pytest.fail(f"Experiment {job_id} did not finish within {timeout}s")


def _sentinel_data():
    # Real production-like rows demonstrate the worker never populates or
    # clears the database serving the administrator's current application.
    with session_scope() as db:
        employee = m.Employee(emp_code="REAL-901", first_name="Actual employee")
        device = m.Device(sn="REAL-DEVICE", alias="Actual terminal")
        db.add_all([employee, device])
        db.flush()
        db.add(m.Transaction(emp_code=employee.emp_code, employee_id=employee.id,
                             punch_time=datetime(2026, 9, 1, 8), device_sn=device.sn))


def test_experiment_page_requires_authenticated_administrator(client):
    # This also demonstrates the missing-route gap before feature integration.
    response = client.get(BASE)
    assert response.status_code == 200, response.text
    assert response.json()["write_back"] is True


def test_small_experiment_runs_and_downloads_without_touching_main_data(client, experiment_manager):
    _sentinel_data()
    before = _main_state()
    response = client.post(BASE, json=SMALL)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id)
    # The user's checked safety option takes effect when the job is accepted.
    assert _main_state() == {**before, "write_back": False}

    job = _wait(client, job_id)
    assert job["status"] == "passed", job
    result = job["result"]
    assert result["status"] == "passed"
    assert (result["employees"], result["devices"], result["days"], result["punches"]) == (20, 2, 2, 80)
    assert result["isolated_temporary_database"] is True
    assert result["physical_devices_verified"] is False
    assert result["ingestion"]["punches_before"] == result["ingestion"]["punches_after"] == 80
    assert result["ingestion"]["http_200_replies"] == 2
    assert result["export"]["data_rows"] == 80
    assert result["reports"]["summary"]["total"] == 20
    assert result["reports"]["daily"]["total"] == 40
    assert job["preview"]["employees"] and job["preview"]["daily"]
    assert job["downloads"] == {"json": True, "csv": True}
    assert not any(path.is_dir() for path in (experiment_manager.root / job_id).iterdir())

    downloaded = client.get(f"{BASE}/{job_id}/download", params={"kind": "json"})
    assert downloaded.status_code == 200, downloaded.text
    assert json.loads(downloaded.content) == result
    assert "attachment" in downloaded.headers["content-disposition"]
    csv_response = client.get(f"{BASE}/{job_id}/download", params={"kind": "csv"})
    assert csv_response.status_code == 200, csv_response.text
    rows = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert len(rows) == 81
    assert all(not cell.startswith(("=", "+", "-", "@")) for row in rows for cell in row)
    for unsafe_kind in ("../job.json", "worker.log", "config.json", "sqlite", ""):
        assert client.get(f"{BASE}/{job_id}/download", params={"kind": unsafe_kind}).status_code == 404
    assert _main_state() == {**before, "write_back": False}
    assert client.get(BASE).json()["jobs"][0]["id"] == job_id
    with session_scope() as db:
        audit = db.scalar(select(m.AuditLog).where(m.AuditLog.action == "experiment.start"))
        assert audit is not None and audit.username == "admin"
        assert "employees=20" in audit.detail and "keep_write_off=True" in audit.detail

    deleted = client.delete(f"{BASE}/{job_id}")
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"{BASE}/{job_id}").status_code == 404
    assert client.get(f"{BASE}/{job_id}/download", params={"kind": "json"}).status_code == 404
    assert client.get(BASE).json()["jobs"] == []


def test_unchecked_safety_option_never_enables_or_disables_live_writes(client, experiment_manager):
    for live_flag in (True, False):
        assert client.put("/api/settings", json={"tcp.write_back": live_flag}).status_code == 200
        response = client.post(BASE, json={**SMALL, "employees": 2, "devices": 1,
                                         "export_csv": False, "ingestion": False,
                                         "keep_write_off": False})
        assert response.status_code == 202, response.text
        job = _wait(client, response.json()["id"])
        assert job["status"] == "passed", job
        assert _main_state()["write_back"] is live_flag
        assert job["downloads"] == {"json": True, "csv": False}
        assert client.get(f"{BASE}/{job['id']}/download", params={"kind": "csv"}).status_code == 404


def test_default_options_are_visible_and_cancellable(client, experiment_manager):
    response = client.post(BASE, json={})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["config"] == {"employees": 1000, "devices": 4, "days": 14,
                              "repeat": 3, "export_csv": True, "ingestion": True,
                              "keep_write_off": True}
    cancelled = client.post(f"{BASE}/{job['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert _wait(client, job["id"])["status"] == "cancelled"


@pytest.mark.parametrize("changes", [
    {"employees": 0}, {"employees": -1}, {"employees": 50001},
    {"devices": 0}, {"devices": 101}, {"employees": 1, "devices": 2},
    {"days": 0}, {"days": 366}, {"repeat": 0}, {"repeat": 6},
    {"employees": 10000, "days": 101},
    {"employees": True}, {"devices": False}, {"days": "2"}, {"repeat": 1.0},
    {"employees": None}, {"export_csv": "false"}, {"ingestion": 1},
    {"keep_write_off": None}, {"output": "../production.db"},
])
def test_invalid_options_do_not_start_or_change_settings(client, experiment_manager, changes):
    before = _main_state()
    response = client.post(BASE, json={**SMALL, **changes})
    assert response.status_code == 422, response.text
    assert client.get(BASE).json()["jobs"] == []
    assert _main_state() == before


@pytest.mark.parametrize("payload", [None, [], "1000 employees", [["employees", 1000]]])
def test_options_body_must_be_a_json_object(client, experiment_manager, payload):
    before = _main_state()
    response = client.post(BASE, json=payload)
    assert response.status_code == 422, response.text
    assert client.get(BASE).json()["jobs"] == []
    assert _main_state() == before


def test_one_active_job_cancel_and_safe_deletion(client, experiment_manager):
    _sentinel_data()
    before = _main_state()
    response = client.post(BASE, json={**SMALL, "employees": 10000,
                                     "devices": 20, "days": 30,
                                     "keep_write_off": False})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    # Cancel after the real worker has opened its disposable database, so the
    # test covers cleanup of interrupted seeding rather than only Popen startup.
    job_folder = experiment_manager.root / job_id
    deadline = time.monotonic() + 10
    while not any(path.is_dir() for path in job_folder.iterdir()):
        assert time.monotonic() < deadline, "Worker did not create its disposable workspace"
        assert client.get(f"{BASE}/{job_id}").json()["status"] in {"queued", "running"}
        time.sleep(0.05)
    assert client.post(BASE, json=SMALL).status_code == 409
    # Rejected jobs cannot trigger the optional change to main-device settings.
    assert _main_state() == before
    assert client.delete(f"{BASE}/{job_id}").status_code == 409
    assert client.post(f"{BASE}/{job_id}/cancel").status_code == 200
    job = _wait(client, job_id)
    assert job["status"] == "cancelled", job
    assert job["downloads"] == {"json": False, "csv": False}
    assert client.get(f"{BASE}/{job_id}/download", params={"kind": "json"}).status_code == 404
    assert _main_state() == before
    assert not any(path.is_dir() for path in job_folder.iterdir()), "Cancelled seed left a database or credentials behind"
    assert client.delete(f"{BASE}/{job_id}").status_code == 200


@pytest.mark.parametrize("job_id", ["bad", "0" * 32, "..", "1234%2fresult.json"])
def test_unknown_or_unsafe_job_ids_never_expose_files(client, experiment_manager, job_id):
    for method, suffix in (("get", ""), ("post", "/cancel"),
                           ("delete", ""), ("get", "/download?kind=json")):
        response = getattr(client, method)(f"{BASE}/{job_id}{suffix}")
        assert response.status_code == 404, response.text


def test_all_experiment_operations_require_admin(client, experiment_manager):
    viewer = next(row for row in client.get("/api/roles").json()["rows"] if row["name"] == "Viewer")
    response = client.post("/api/users", json={"username": "experiment-viewer",
        "password": "viewer-secret1", "role_id": viewer["id"]})
    assert response.status_code == 200, response.text
    with TestClient(app) as viewer_client, TestClient(app) as anonymous:
        assert viewer_client.post("/api/auth/login", json={
            "username": "experiment-viewer", "password": "viewer-secret1"}).status_code == 200
        for restricted, status in ((viewer_client, 403), (anonymous, 401)):
            for method, suffix, payload in (("get", "", None), ("post", "", SMALL),
                ("get", "/" + "0" * 32, None), ("post", "/" + "0" * 32 + "/cancel", None),
                ("delete", "/" + "0" * 32, None),
                ("get", "/" + "0" * 32 + "/download?kind=json", None)):
                response = restricted.request(method, BASE + suffix, json=payload)
                assert response.status_code == status, response.text
    assert client.get(BASE).json()["jobs"] == []


def test_ui_limits_are_explicit(client, experiment_manager):
    response = client.get(BASE)
    assert response.status_code == 200, response.text
    assert response.json()["limits"] == {"employees": 50000, "devices": 100,
                                         "days": 365, "repeat": 5, "punches": 2000000}


def test_missing_worker_cannot_turn_off_writes_or_create_an_accepted_job(client, tmp_path, monkeypatch):
    from hader.experiments import ExperimentManager
    from hader.api import experiments

    manager = ExperimentManager(tmp_path / "jobs", worker_path=tmp_path / "missing_worker.py")
    monkeypatch.setattr(experiments, "get_manager", lambda: manager)
    try:
        before = _main_state()
        response = client.post(BASE, json=SMALL)
        assert response.status_code == 503, response.text
        assert client.get(BASE).json()["jobs"] == []
        assert _main_state() == before
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(m.AuditLog).where(
                m.AuditLog.action == "experiment.start")) == 0
    finally:
        manager.shutdown()


def test_failed_worker_never_offers_partial_downloads(client, tmp_path, monkeypatch):
    from hader.experiments import ExperimentManager
    from hader.api import experiments

    worker = tmp_path / "failing_worker.py"
    worker.write_text("raise RuntimeError('synthetic worker failure')\n", encoding="utf-8")
    manager = ExperimentManager(tmp_path / "jobs", worker_path=worker)
    monkeypatch.setattr(experiments, "get_manager", lambda: manager)
    try:
        _sentinel_data()
        before = _main_state()
        response = client.post(BASE, json=SMALL)
        assert response.status_code == 202, response.text
        job = _wait(client, response.json()["id"])
        assert job["status"] == "failed" and job["error"]
        assert job["downloads"] == {"json": False, "csv": False}
        assert "result" not in job and "preview" not in job
        for kind in ("json", "csv"):
            assert client.get(f"{BASE}/{job['id']}/download", params={"kind": kind}).status_code == 404
        assert _main_state() == {**before, "write_back": False}
        assert client.delete(f"{BASE}/{job['id']}").status_code == 200
    finally:
        manager.shutdown()


def test_process_launch_oserror_is_friendly_and_preserves_accepted_safety_option(
        client, experiment_manager, tmp_path, monkeypatch):
    import hader.experiments as worker_manager

    _sentinel_data()
    before = _main_state()
    missing_interpreter = str(tmp_path / "private-python-install" / "not-present")
    monkeypatch.setattr(worker_manager.sys, "executable", missing_interpreter)
    response = client.post(BASE, json=SMALL)
    assert response.status_code == 503, response.text
    assert "Could not start the experiment process" in response.json()["detail"]
    assert missing_interpreter not in response.text
    assert str(experiment_manager.root) not in response.text
    assert "FileNotFoundError" not in response.text and "[Errno" not in response.text
    assert client.get(BASE).json()["jobs"] == []
    assert list(experiment_manager.root.iterdir()) == []
    assert not experiment_manager._processes
    # The accepted administrator choice remains safe even if Python cannot
    # start. Recovery must never silently re-enable writes on physical devices.
    assert _main_state() == {**before, "write_back": False}


def test_shutdown_stops_running_worker_and_removes_disposable_database(client, experiment_manager):
    from hader.experiments import ExperimentError

    _sentinel_data()
    before = _main_state()
    response = client.post(BASE, json={**SMALL, "employees": 10000,
                                     "devices": 20, "days": 30,
                                     "keep_write_off": False})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    job_folder = experiment_manager.root / job_id
    deadline = time.monotonic() + 10
    while not any(path.is_dir() for path in job_folder.iterdir()):
        assert time.monotonic() < deadline, "Worker did not create its disposable workspace"
        assert client.get(f"{BASE}/{job_id}").json()["status"] in {"queued", "running"}
        time.sleep(0.05)
    # Retain the real child handle to verify process and pipe cleanup, rather
    # than inferring cleanup solely from the administrator's status label.
    process = experiment_manager._processes[job_id][0]
    assert process.poll() is None and process.stdin is not None
    experiment_manager.shutdown()
    assert process.poll() is not None and process.stdin.closed
    assert experiment_manager.get(job_id)["status"] == "cancelled"
    assert not any(path.is_dir() for path in job_folder.iterdir())
    assert _main_state() == before
    with pytest.raises(ExperimentError, match="Server is stopping"):
        experiment_manager.start(SMALL)
