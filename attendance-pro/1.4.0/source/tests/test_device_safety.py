"""Device recovery and write safeguards exercised without a live terminal."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from hader import models as m, store, tcp_pull
from hader.adms import commands as C, sync
from hader.db import now, session_scope
from hader.terminal.client import TerminalError


def _device(client, sn="SAFE", **extra):
    response = client.post("/api/devices", json={"sn": sn, "area_id": 1, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_full_read_recovers_old_archive_after_a_future_high_watermark(client):
    _device(client)
    old, recent = datetime(2024, 1, 2, 8), datetime(2026, 10, 5, 8)
    with session_scope() as db:
        dev = db.scalar(select(m.Device).where(m.Device.sn == "SAFE"))
        sync.save_punches(db, dev, [sync.AttRecord("11", recent)])
    data = tcp_pull.DeviceRead(punches=[sync.AttRecord("11", old), sync.AttRecord("11", recent)])
    tcp_pull.store_read("SAFE", data)
    with session_scope() as db:
        assert set(db.scalars(select(m.Transaction.punch_time)).all()) == {old, recent}


def test_read_only_cannot_be_overridden_by_owning_the_port_or_point_server(client, monkeypatch):
    from hader.adms import ports
    _device(client, ip="127.0.0.1")
    client.put("/api/settings", json={"tcp.write_back": False})
    monkeypatch.setattr(ports, "PORTS", {90: {"mode": "own"}})
    with session_scope() as db:
        assert tcp_pull.writing_enabled(db) is False
    called = []
    monkeypatch.setattr(tcp_pull, "point_to_this_server", lambda *a: called.append(a))
    dev_id = client.get("/api/devices").json()["rows"][0]["id"]
    response = client.post(f"/api/devices/{dev_id}/server", json={"ip": "10.0.0.2"})
    assert response.status_code == 409 and not called


def test_pending_adms_writes_wait_when_permission_is_disabled_but_queries_work(client):
    _device(client)
    with session_scope() as db:
        write = sync.queue(db, "SAFE", "DATA DELETE USERINFO PIN=11")
        read = sync.queue(db, "SAFE", "INFO")
        write_id, read_id = write.id, read.id
    client.put("/api/settings", json={"tcp.write_back": False})
    reply = client.get("/iclock/getrequest", params={"SN": "SAFE"}).text
    assert f"C:{read_id}:INFO" in reply and f"C:{write_id}:" not in reply


def test_device_command_result_requires_known_enabled_serial(client):
    device = _device(client)
    with session_scope() as db:
        cmd = sync.queue(db, "SAFE", "INFO")
        cid = cmd.id
    raw = f"ID={cid}&Return=0&CMD=INFO".encode()
    assert client.post("/iclock/devicecmd", content=raw).status_code == 400
    assert client.post("/iclock/devicecmd", params={"SN": "STRANGER"}, content=raw).status_code == 403
    client.put(f"/api/devices/{device['id']}", json={"enabled": False})
    assert client.post("/iclock/devicecmd", params={"SN": "SAFE"}, content=raw).status_code == 403
    with session_scope() as db:
        assert db.get(m.DeviceCommand, cid).status == "pending"


def test_a_device_cannot_ack_another_devices_command(client):
    _device(client, "SAFE")
    _device(client, "OTHER")
    with session_scope() as db:
        cid = sync.queue(db, "SAFE", "INFO").id
    client.post("/iclock/devicecmd", params={"SN": "OTHER"}, content=f"ID={cid}&Return=0&CMD=INFO".encode())
    with session_scope() as db:
        assert db.get(m.DeviceCommand, cid).status == "pending"


@pytest.mark.parametrize("content", ["CLEAR LOG", "CLEAR DATA", "CLEAR PHOTO", "DATA DELETE USERINFO PIN=11"])
def test_destructive_commands_with_lost_reply_are_never_automatically_replayed(client, content):
    _device(client)
    with session_scope() as db:
        cmd = sync.queue(db, "SAFE", content)
        cmd.status, cmd.sent_at, cmd.attempts = "sent", now() - timedelta(minutes=20), 1
        cid = cmd.id
    with session_scope() as db:
        sync.requeue_stale(db)
    with session_scope() as db:
        assert db.get(m.DeviceCommand, cid).status == "failed"
        assert "manual" in db.get(m.DeviceCommand, cid).result.lower()


def test_failed_direct_write_always_reenables_and_disconnects(client, monkeypatch):
    _device(client, ip="127.0.0.1")
    with session_scope() as db:
        sync.queue(db, "SAFE", "DATA DELETE USERINFO PIN=11")
    calls = []

    class BrokenRead:
        def enable(self, on=True):
            calls.append(("enable", on))

        def get_users(self):
            raise TerminalError("broken read")

        def disconnect(self):
            calls.append(("disconnect",))

    monkeypatch.setattr(tcp_pull, "_client", lambda *a: BrokenRead())
    tcp_pull.deliver_pending("SAFE")
    assert calls == [("enable", False), ("enable", True), ("disconnect",)]


def test_prefixless_userinfo_and_fingertmp_are_imported_in_the_correct_tables(client):
    _device(client)
    client.post("/iclock/cdata", params={"SN": "SAFE", "table": "USERINFO"},
                content=b"PIN=77\tName=Ali")
    client.post("/iclock/cdata", params={"SN": "SAFE", "table": "FINGERTMP"},
                content=b"PIN=77\tFID=6\tValid=1\tTMP=QUFB")
    with session_scope() as db:
        employee = db.scalar(select(m.Employee).where(m.Employee.emp_code == "77"))
        assert employee.full_name == "Ali"
        template = db.scalar(select(m.BioTemplate).where(m.BioTemplate.employee_id == employee.id))
        assert (template.bio_type, template.bio_no) == (1, 6)


def test_delete_biodata_preserves_individual_no_and_index():
    device = m.Device(options=json.dumps({"MultiBioDataSupport": "0:1"}))
    command = sync.template_delete_command(device, "77", 1, 6, 2)
    assert "No=6" in command and "Index=2" in command and "Type=1" in command


@pytest.mark.parametrize("key,kind", [("MaxUserCount", "users"), ("MaxFingerCount", "fingerprints"),
                                     ("MaxFaceCount", "faces")])
def test_bulk_sync_fails_before_queueing_when_reported_capacity_is_exceeded(client, key, kind):
    _device(client)
    with session_scope() as db:
        device = db.scalar(select(m.Device).where(m.Device.sn == "SAFE"))
        device.options = json.dumps({key: "1", "MultiBioDataSupport": "0:1:0:0:0:0:0:0:0:1"})
        area = db.get(m.Area, 1)
        for code in ("21", "22"):
            employee = m.Employee(emp_code=code, first_name=code, areas=[area])
            db.add(employee)
            db.flush()
            db.add(m.BioTemplate(employee_id=employee.id, bio_type=1, bio_no=0, template="QUFB"))
            db.add(m.BioTemplate(employee_id=employee.id, bio_type=9, bio_no=0, template="QUFB"))
        db.flush()
        before = db.query(m.DeviceCommand).count()
        with pytest.raises(sync.SyncCapacityError) as error:
            sync.sync_device(db, device)
        assert kind in str(error.value)
        assert error.value.limit == 1 and error.value.requested == 2
        assert db.query(m.DeviceCommand).count() == before


def test_adms_ip_acl_is_explicit_and_dhcp_remains_allowed_without_it(client):
    device = _device(client, adms_allowed_ips=["192.0.2.0/24"])
    response = client.post("/iclock/cdata", params={"SN": "SAFE", "table": "USERINFO"},
                           content=b"PIN=88\tName=Blocked")
    assert response.status_code == 403
    client.put(f"/api/devices/{device['id']}", json={"adms_allowed_ips": []})
    assert client.post("/iclock/cdata", params={"SN": "SAFE", "table": "USERINFO"},
                       content=b"PIN=88\tName=Allowed").status_code == 200
