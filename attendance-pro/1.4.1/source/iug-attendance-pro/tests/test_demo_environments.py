"""Exercise isolated demo processes through real HTTP and their owner lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from hader import models as m, store
from hader.db import session_scope
from hader.demo_environments import DemoManager, validate_options
from hader.experiments import ExperimentError


@pytest.mark.parametrize("options", [
    {"employees": True}, {"employees": "10"}, {"employees": 1.2}, {"employees": 0},
    {"employees": 50001}, {"devices": 101}, {"days": 366}, {"days": -1},
    {"employees": 3, "devices": 4}, {"employees": 50000, "devices": 20, "days": 21},
    {"tcp.write_back": False}, {"employees": 10, "devices": 1, "days": None},
])
def test_demo_limits_are_strict(options):
    with pytest.raises(ValueError):
        validate_options(options)


def _wait(manager, job_id, expected="running", timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] == expected:
            return job
        if job["status"] == "failed" and expected != "failed":
            folder = manager.root / job_id
            pytest.fail(f"Demo worker failed: {job}; {(folder / 'worker.log').read_text(encoding='utf-8', errors='replace')}")
        time.sleep(0.05)
    pytest.fail(f"Demo did not become {expected}: {manager.get(job_id)}")


@pytest.fixture()
def demo_manager(tmp_path):
    manager = DemoManager(tmp_path / "demos")
    try:
        yield manager
    finally:
        manager.shutdown()


def test_real_full_app_demo_isolation_and_physical_action_guard(client, demo_manager, monkeypatch, tmp_path):
    from hader.api import demo_environments as api
    monkeypatch.setattr(api, "get_manager", lambda: demo_manager)
    private = tmp_path / "production_secret"
    private.write_bytes(b"private production credentials")
    original_hash = hashlib.sha256(private.read_bytes()).hexdigest()
    monkeypatch.setenv("HADER_SECRET_KEY", "production-secret-never-inherit")
    monkeypatch.setenv("HADER_DATA_DIR", str(private.parent))
    with session_scope() as db:
        db.add(m.Employee(emp_code="99999", first_name="Production sentinel"))
        db.add(m.Device(sn="PRODUCTION", ip="192.0.2.44"))
        store.set_(db, "tcp.write_back", True)
    response = client.post("/api/demo-environments", json={"employees": 24, "devices": 3, "days": 3})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert response.json()["status"] == "starting"
    job = _wait(demo_manager, job_id)
    detail = client.get(f"/api/demo-environments/{job_id}").json()
    assert detail["open_url"].endswith("#demo-access=" + job["access_token"])
    assert "access_token" not in detail
    busy = client.post("/api/demo-environments", json={"employees": 2, "devices": 1, "days": 1})
    assert busy.status_code == 409
    assert client.delete(f"/api/demo-environments/{job_id}").status_code == 409
    before_cookie = dict(client.cookies)
    with httpx.Client(base_url=f"http://127.0.0.1:{job['port']}", trust_env=False, timeout=15) as demo:
        info = demo.get("/api/demo/info").json()
        assert info["is_demo"] is True
        assert demo.get("/api/auth/me").status_code == 401
        assert demo.post("/api/demo/access", json={"token": "wrong"}).status_code == 401
        assert demo.post("/api/demo/access", json={"token": job["access_token"]}).status_code == 200
        assert set(demo.cookies) == {"hader_demo_" + job_id}
        assert demo.get("/api/auth/me").json()["must_change_password"] is False
        employees = demo.get("/api/employees").json()
        assert employees["total"] == 24
        assert all(row["emp_code"] != "99999" for row in employees["rows"])
        devices = demo.get("/api/devices").json()
        assert devices["total"] == 3
        assert all(not row["ip"] and not row["tcp_poll"] for row in devices["rows"])
        settings = demo.get("/api/settings").json()
        for key in ("tcp.write_back", "tcp.read_bio", "discovery.enabled", "alerts.enabled", "adms.auto_add"):
            assert settings[key] is False
        for method, path, body in [
            ("GET", "/iclock/cdata?SN=SCALE0000", None),
            ("POST", "/iclock/cdata?SN=REAL", None),
            ("POST", "/api/devices/discover", {}),
            ("POST", "/api/devices/probe", {"ip": "127.0.0.1"}),
            ("POST", "/api/devices/1/pull", {}),
            ("GET", "/api/devices/1/server", None),
            ("GET", "/api/devices/1/panel", None),
            ("GET", "/api/devices/1/panel/option?key=Clock", None),
            ("POST", "/api/devices/1/action", {"action": "reboot"}),
            ("POST", "/api/devices/1/panel/time", {}),
            ("POST", "/api/employees/1/enroll", {}),
            ("POST", "/api/employees/1/pull-bio", {}),
            ("POST", "/api/link-mode", {"action": "take"}),
            ("PUT", "/api/system/network", {"web_port": 8081}),
            ("POST", "/api/operations/devices/1/clock-check", {}),
            ("POST", "/api/operations/backup-check", {}),
            ("PUT", "/api/settings", {"backup.offsite_path": "/tmp/outside"}),
            ("POST", "/api/alerts/test", {"channel": "email"}),
            ("PUT", "/api/settings", {"tcp.write_back": True}),
            ("PUT", "/api/settings", {"discovery.enabled": True}),
            ("PUT", "/api/devices/1", {"ip": "127.0.0.1"}),
            ("POST", "/api/demo-environments", {"employees": 2}),
        ]:
            blocked = demo.request(method, path, json=body)
            assert blocked.status_code == 409, (method, path, blocked.text)
            assert blocked.json()["detail"]["code"] == "demo_device_access_disabled"
        assert demo.put("/api/settings", json={"att.dup_punch_minutes": 2, "tcp.write_back": False}).status_code == 200
        commands = demo.get("/api/device-commands").json()
        assert commands["rows"] and all(row["status"] == "done" for row in commands["rows"])
        created = demo.post("/api/demo/simulate", json={"count": 8})
        assert created.status_code == 200, created.text
        assert created.json()["created"] == 8
        transactions = demo.get("/api/transactions", params={"start": created.json()["today"], "end": created.json()["today"]}).json()
        assert transactions["total"] == job["counts"]["today_transactions"] + 8
        assert all(row["punch_time"][:10] == created.json()["today"] for row in transactions["rows"])
        cutoff = demo.get("/api/demo/info").json()["server_local_time"].replace("T", " ")
        assert all(row["punch_time"] <= cutoff for row in transactions["rows"])
        monitor = demo.get("/api/monitor").json()
        assert monitor["total"] == transactions["total"]
        assert {row["id"] for row in monitor["rows"]} == {row["id"] for row in transactions["rows"]}
        assert demo.post("/api/demo/simulate", json={"count": True}).status_code == 422
    assert dict(client.cookies) == before_cookie
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Employee)) == 1
        assert db.scalar(select(func.count()).select_from(m.Device)) == 1
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 0
        assert store.get(db, "tcp.write_back") is True
    assert hashlib.sha256(private.read_bytes()).hexdigest() == original_hash
    data_folder = demo_manager.root / job_id / "data"
    assert not (data_folder / "initial_admin_password.txt").exists()
    assert not (data_folder / ".secret_key").exists()
    assert client.post(f"/api/demo-environments/{job_id}/stop").json()["status"] == "stopped"
    assert not (demo_manager.root / job_id / "access.json").exists()
    assert "open_url" not in client.get(f"/api/demo-environments/{job_id}").json()
    assert client.delete(f"/api/demo-environments/{job_id}").status_code == 200
    assert not (demo_manager.root / job_id).exists()


def test_demo_routes_require_admin_and_do_not_modify_production_write_flag(client, demo_manager, monkeypatch):
    from hader.api import demo_environments as api
    monkeypatch.setattr(api, "get_manager", lambda: demo_manager)
    with session_scope() as db:
        user = db.scalar(select(m.User).where(m.User.username == "admin"))
        user.is_superuser = False
        user.role_id = None
    assert client.get("/api/demo-environments").status_code == 403
    assert client.post("/api/demo-environments", json={}).status_code == 403
    client.cookies.clear()
    assert client.get("/api/demo-environments").status_code == 401
    assert client.get("/api/demo/info").json() == {"is_demo": False}
    assert client.post("/api/demo/access", json={"token": "x"}).status_code == 404


def test_demo_unknown_id_and_options(demo_manager):
    assert demo_manager.list() == []
    for job_id in ("../", "00" * 16, "bad", None):
        with pytest.raises(ExperimentError) as error:
            demo_manager.get(job_id)
        assert error.value.status == 404
    demo_manager.shutdown()
    with pytest.raises(ExperimentError):
        demo_manager.start({"employees": 2, "devices": 1, "days": 1})


def test_demo_restart_marks_owned_jobs_interrupted_and_revokes_access(tmp_path):
    root = tmp_path / "demos"
    folder = root / ("ab" * 16)
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"id": folder.name, "status": "running", "created_at": "2026-01-01"}), encoding="utf-8")
    (folder / "access.json").write_text('{"token":"obsolete"}', encoding="utf-8")
    manager = DemoManager(root)
    assert manager.get(folder.name)["status"] == "interrupted"
    assert "access_token" not in manager.get(folder.name)
    assert not (folder / "access.json").exists()
    manager.shutdown()


def test_demo_session_deadline_stops_without_browser_polling(demo_manager, monkeypatch):
    import hader.demo_environments as module
    monkeypatch.setattr(module, "SESSION_SECONDS", 1.5)
    job = demo_manager.start({"employees": 2, "devices": 1, "days": 1})
    process = demo_manager._processes[job["id"]][0]
    process.wait(timeout=15)
    deadline = time.monotonic() + 5
    while demo_manager._processes and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not demo_manager._processes
    metadata = json.loads((demo_manager.root / job["id"] / "job.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "stopped"
    assert "expired" in metadata["error"]


def test_demo_child_stops_after_owner_pipe_closes(demo_manager):
    job = demo_manager.start({"employees": 2, "devices": 1, "days": 1})
    ready = _wait(demo_manager, job["id"])
    process = demo_manager._processes[job["id"]][0]
    process.stdin.close()
    process.wait(timeout=10)
    assert process.returncode in {0, 3}
    with pytest.raises(httpx.TransportError):
        httpx.get(f"http://127.0.0.1:{ready['port']}/api/demo/info", timeout=2, trust_env=False)


def test_demo_institutional_time_zone_is_explicit(demo_manager):
    demo_manager.time_zone = "Asia/Hebron"
    job = demo_manager.start({"employees": 2, "devices": 1, "days": 1})
    ready = _wait(demo_manager, job["id"])
    from datetime import datetime
    from zoneinfo import ZoneInfo
    with httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False) as demo:
        assert demo.get("/api/demo/info").json()["server_local_time"][:10] == datetime.now(ZoneInfo("Asia/Hebron")).date().isoformat()
    config = json.loads((demo_manager.root / job["id"] / "config.json").read_text(encoding="utf-8"))
    assert config["time_zone"] == "Asia/Hebron"
