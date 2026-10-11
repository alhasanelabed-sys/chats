"""Durable ATTLOG diagnostics preserve device uploads before acknowledging them."""
from datetime import timedelta
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from hader import intake, models as m, store
from hader.app import app
from hader.db import engine, session_scope


def _upload(client, raw, sn="INBOX1", stamp="44"):
    return client.post("/iclock/cdata", params={"SN": sn, "table": "ATTLOG", "Stamp": stamp}, content=raw)


def test_good_and_rejected_lines_survive_acknowledgement(client):
    raw = b"77\t2026-10-10 08:00:00\t0\t15\t0\n88\twrong time\t0\t15\t0\nmalformed\n"
    uploaded = _upload(client, raw)
    assert uploaded.status_code == 200 and uploaded.text == "OK: 1"
    response = client.get("/api/intake")
    assert response.status_code == 200, response.text
    batch = response.json()["rows"][0]
    assert (batch["received"], batch["accepted"], batch["duplicates"], batch["rejected"]) == (3, 1, 0, 2)
    assert batch["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert batch["source_sn"] == "INBOX1" and batch["source_stamp"] == "44"
    assert batch["received_at_utc"].endswith("Z")
    detail = client.get(f"/api/intake/{batch['id']}").json()
    assert [line["status"] for line in detail["line_results"]] == ["accepted", "rejected", "rejected"]
    assert [line.get("reason") for line in detail["line_results"]] == [None, "invalid_timestamp", "missing_fields"]
    download = client.get(f"/api/intake/{batch['id']}/raw")
    assert download.status_code == 200 and download.content == raw
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 1
        assert db.scalar(select(m.Device.att_stamp).where(m.Device.sn == "INBOX1")) == "44"


def test_retry_and_duplicates_within_one_payload_are_counted_correctly(client):
    raw = b"77\t2026-10-10 08:00:00\t0\t15\t0\n" * 2
    assert _upload(client, raw).text == "OK: 2"
    first = client.get("/api/intake").json()["rows"][0]
    assert (first["accepted"], first["duplicates"], first["rejected"]) == (1, 1, 0)
    assert _upload(client, raw).text == "OK: 2"
    second = client.get("/api/intake").json()["rows"][0]
    assert (second["accepted"], second["duplicates"], second["rejected"]) == (0, 2, 0)
    quality = client.get("/api/intake").json()["quality"][0]
    assert (quality["batches"], quality["received"], quality["accepted"], quality["duplicates"]) == (2, 4, 1, 3)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Employee)) == 1
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 1


def test_corrected_replay_preserves_original_and_does_not_queue_commands(client):
    raw = b"77\t2026-10-10 08:00:00\t0\t15\t0\n88\twrong time\t0\t15\t0\nmalformed\n"
    assert _upload(client, raw).text == "OK: 1"
    batch = client.get("/api/intake").json()["rows"][0]
    client.put("/api/settings", json={"tcp.write_back": False})
    with session_scope() as db:
        commands_before = db.scalar(select(func.count()).select_from(m.DeviceCommand))
        device_before = db.scalar(select(m.Device).where(m.Device.sn == "INBOX1"))
        stamp, activity = device_before.att_stamp, device_before.last_activity
    # Closing all connections demonstrates this is a durable inbox, not memory.
    engine.dispose()
    unchanged = client.post(f"/api/intake/{batch['id']}/replay", json={})
    assert unchanged.status_code == 200, unchanged.text
    assert (unchanged.json()["accepted"], unchanged.json()["duplicates"], unchanged.json()["rejected"]) == (0, 1, 2)
    corrected = "77\t2026-10-10 08:00:00\t0\t15\t0\n88\t2026-10-10 09:00:00\t0\t15\t0\n99\t2026-10-10 10:00:00\t0\t15\t0\n"
    replay = client.post(f"/api/intake/{batch['id']}/replay", json={"corrected_body": corrected})
    assert replay.status_code == 200, replay.text
    assert replay.json()["replay_of"] == batch["id"]
    assert replay.json()["direction"] == "server_database_replay"
    assert (replay.json()["accepted"], replay.json()["duplicates"], replay.json()["rejected"]) == (2, 1, 0)
    repeated = client.post(f"/api/intake/{batch['id']}/replay", json={"corrected_body": corrected})
    assert (repeated.json()["accepted"], repeated.json()["duplicates"]) == (0, 3)
    original = client.get(f"/api/intake/{batch['id']}").json()
    assert original["unresolved_rejected"] == 0
    assert original["rejected"] == 2 and original["accepted"] == 1 and original["replay_count"] == 3
    assert len(original["replay_history"]) == 3
    assert client.get(f"/api/intake/{batch['id']}/raw").content == raw
    with session_scope() as db:
        assert store.get(db, "tcp.write_back") is False
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 3
        assert db.scalar(select(func.count()).select_from(m.Employee)) == 3
        assert db.scalar(select(func.count()).select_from(m.DeviceCommand)) == commands_before
        device_after = db.scalar(select(m.Device).where(m.Device.sn == "INBOX1"))
        assert (device_after.att_stamp, device_after.last_activity, device_after.managed_by) == (stamp, activity, None)
        audit = db.scalar(select(m.AuditLog).where(m.AuditLog.action == "intake.replay").order_by(m.AuditLog.id.desc()))
        assert audit.username == "admin"
        detail = json.loads(audit.detail)
        assert detail["device_communication"] is False and detail["original_sha256"] == batch["payload_sha256"]


def test_omitting_or_changing_rejected_person_does_not_hide_unresolved_data(client):
    raw = b"77\t2026-10-10 08:00:00\n88\twrong\n"
    _upload(client, raw)
    batch_id = client.get("/api/intake").json()["rows"][0]["id"]
    for body in ("77\t2026-10-10 08:00:00\n", "77\t2026-10-10 08:00:00\n99\t2026-10-10 09:00:00\n"):
        assert client.post(f"/api/intake/{batch_id}/replay", json={"corrected_body": body}).status_code == 200
        assert client.get(f"/api/intake/{batch_id}").json()["unresolved_rejected"] == 1
    assert client.get("/api/intake", params={"rejected_only": True}).json()["rows"][0]["id"] == batch_id


def test_reader_permissions_hide_original_bytes_and_disallow_replay(client):
    _upload(client, b"77\tinvalid\n")
    batch_id = client.get("/api/intake").json()["rows"][0]["id"]
    viewer = next(role for role in client.get("/api/roles").json()["rows"] if role["name"] == "Viewer")
    assert client.post("/api/users", json={"username": "intake-viewer", "password": "viewer-secret1", "role_id": viewer["id"]}).status_code == 200
    reader = TestClient(app)
    assert reader.post("/api/auth/login", json={"username": "intake-viewer", "password": "viewer-secret1"}).status_code == 200
    assert reader.get("/api/intake").status_code == 200
    assert reader.get(f"/api/intake/{batch_id}").status_code == 200
    assert reader.get(f"/api/intake/{batch_id}/raw").status_code == 403
    assert reader.post(f"/api/intake/{batch_id}/replay", json={}).status_code == 403
    anonymous = TestClient(app)
    assert anonymous.get("/api/intake").status_code == 401
    assert anonymous.get(f"/api/intake/{batch_id}").status_code == 401
    assert anonymous.get(f"/api/intake/{batch_id}/raw").status_code == 401
    assert anonymous.post(f"/api/intake/{batch_id}/replay", json={}).status_code == 401


@pytest.mark.parametrize("data", [{"corrected_body": None}, {"corrected_body": 123}, {"corrected_body": ""},
                                  {"corrected_body": "   "}, {"unknown": "x"}, [], "bad"])
def test_replay_rejects_invalid_options_without_extra_batches(client, data):
    _upload(client, b"77\tinvalid\n")
    batch_id = client.get("/api/intake").json()["rows"][0]["id"]
    response = client.post(f"/api/intake/{batch_id}/replay", json=data)
    assert response.status_code == 422, response.text
    assert client.get("/api/intake").json()["total"] == 1


def test_replay_rejects_non_json_and_oversized_corrected_body(client):
    _upload(client, b"77\tinvalid\n")
    batch_id = client.get("/api/intake").json()["rows"][0]["id"]
    assert client.post(f"/api/intake/{batch_id}/replay", content=b"{broken").status_code == 422
    huge = "A" * (intake.MAX_PAYLOAD_BYTES + 1)
    assert client.post(f"/api/intake/{batch_id}/replay", json={"corrected_body": huge}).status_code == 413
    assert client.get("/api/intake").json()["total"] == 1


@pytest.mark.parametrize("raw,reason", [(b"bad code!\t2026-10-10 08:00:00\n", "invalid_employee_code"),
                                       (b"77\t2026-10-10 08:00:00\tNaN\t15\n", "invalid_state"),
                                       (b"77\t2026-10-10 08:00:00\t0\t-1\n", "invalid_verify"),
                                       (b"\t2026-10-10 08:00:00\n", "missing_employee_code")])
def test_invalid_fields_are_rejected_with_specific_diagnostics(client, raw, reason):
    assert _upload(client, raw).status_code == 200
    batch = client.get("/api/intake").json()["rows"][0]
    assert (batch["accepted"], batch["duplicates"], batch["rejected"]) == (0, 0, 1)
    assert client.get(f"/api/intake/{batch['id']}").json()["line_results"][0]["reason"] == reason
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Employee)) == 0
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 0


def test_oversized_and_non_attendance_uploads_are_not_acknowledged_or_archived(client):
    assert _upload(client, b"A" * (intake.MAX_PAYLOAD_BYTES + 1)).status_code == 413
    assert _upload(client, b"malformed\n" * (intake.MAX_LINES + 1)).status_code == 413
    assert _upload(client, b"USER PIN=77\tName=Private\tPasswd=secret\n").status_code == 422
    assert _upload(client, b"FP PIN=77\tTMP=QUJD==\n").status_code == 422
    assert client.get("/api/intake").json()["total"] == 0
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Device)) == 0


def test_insert_failure_rolls_back_raw_upload_punches_employee_and_stamp(client):
    from hader.adms import server
    _upload(client, b"77\t2026-10-10 08:00:00\n", stamp="11")
    with session_scope() as db:
        before = db.scalar(select(m.Device.last_activity).where(m.Device.sn == "INBOX1"))

    def fail_punch_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith('INSERT INTO "transaction"'):
            raise RuntimeError("test database write failure")

    event.listen(engine, "before_cursor_execute", fail_punch_insert)
    try:
        with pytest.raises(RuntimeError, match="test database write failure"):
            server.handle_upload("INBOX1", "testclient", "ATTLOG", "99", b"99\t2026-10-10 09:00:00\n88\twrong\n")
    finally:
        event.remove(engine, "before_cursor_execute", fail_punch_insert)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(intake.IntakeBatch)) == 1
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 1
        assert db.scalar(select(func.count()).select_from(m.Employee)) == 1
        device = db.scalar(select(m.Device).where(m.Device.sn == "INBOX1"))
        assert (device.att_stamp, device.last_activity) == ("11", before)


def test_retention_prunes_clean_batches_but_keeps_rejects_and_replay_evidence(client):
    _upload(client, b"77\twrong\n")
    bad_id = client.get("/api/intake").json()["rows"][0]["id"]
    assert client.post(f"/api/intake/{bad_id}/replay", json={"corrected_body": "77\t2026-10-10 08:00:00\n"}).status_code == 200
    _upload(client, b"88\t2026-10-10 09:00:00\n")
    clean_id = client.get("/api/intake").json()["rows"][0]["id"]
    with session_scope() as db:
        old = intake.utc_now() - timedelta(days=40)
        for batch in db.scalars(select(intake.IntakeBatch)):
            batch.received_at_utc = old
        assert intake.prune_accepted(db) == 1
    assert client.get(f"/api/intake/{clean_id}").status_code == 404
    original = client.get(f"/api/intake/{bad_id}").json()
    assert original["rejected"] == 1 and original["unresolved_rejected"] == 0
    assert original["replay_history"]
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(m.Transaction)) == 2


def test_batch_and_line_pagination_serials_and_missing_ids(client):
    _upload(client, b"77\twrong\n88\twrong\n99\twrong\n", sn="SERIAL-A")
    _upload(client, b"77\t2026-10-10 08:00:00\n", sn="SERIAL-B")
    filtered = client.get("/api/intake", params={"sn": "SERIAL-A", "limit": 1}).json()
    assert filtered["total"] == 1 and filtered["quality"][0]["source_sn"] == "SERIAL-A"
    batch_id = filtered["rows"][0]["id"]
    lines = client.get(f"/api/intake/{batch_id}", params={"offset": 1, "limit": 1}).json()
    assert lines["line_total"] == 3 and len(lines["line_results"]) == 1
    assert lines["line_results"][0]["emp_code"] == "88"
    page = client.get("/api/intake", params={"offset": 1, "limit": 1}).json()
    assert page["total"] == 2 and len(page["rows"]) == 1
    for path in ("/api/intake/99999", "/api/intake/99999/raw"):
        assert client.get(path).status_code == 404
    assert client.post("/api/intake/99999/replay", json={}).status_code == 404
