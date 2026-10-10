"""End-to-end: simulated terminals talking to the server over ADMS."""
from datetime import datetime, timedelta


def _dev(client, sn):
    return next(d for d in client.get("/api/devices").json()["rows"] if d["sn"] == sn)


def test_register_handshake_and_capabilities(client, device_factory):
    dev = device_factory("V5L0001")
    opts = dev.handshake()
    assert opts["GET OPTION FROM"] == "V5L0001"
    info = _dev(client, "V5L0001")
    assert info["state"] == "online" and info["model"] == "SpeedFace-V5L"
    assert info["firmware"] == "ZAM180-NF50VA-Ver3.0.36"
    assert info["bio_support"] == {"1": "10", "8": "1", "9": "39"}


def test_punches_are_stored_once(client, device_factory):
    dev = device_factory()
    dev.handshake()
    t = datetime(2026, 9, 1, 8, 5)
    dev.punch("77", t)
    dev.punch("77", t)                    # the device re-sends after a lost reply
    dev.upload_all_att()
    rows = client.get("/api/transactions", params={"q": "77"}).json()
    assert rows["total"] == 1
    # Unknown PINs become employees so the punch is not orphaned.
    emps = client.get("/api/employees", params={"q": "77"}).json()["rows"]
    assert emps and emps[0]["emp_code"] == "77"


def test_employee_is_pushed_to_area_devices(client, device_factory):
    a, b = device_factory("DEV_A"), device_factory("DEV_B")
    a.handshake(); b.handshake()
    r = client.post("/api/employees", json={"emp_code": "100", "first_name": "Sara", "last_name": "Ali",
                                            "card_no": "12345", "area_ids": [1]})
    assert r.status_code == 200, r.text
    a.drain(); b.drain()
    assert a.users["100"]["name"] == "Sara Ali" and a.users["100"]["card"] == "12345"
    assert "100" in b.users
    # Moving the employee out of the area removes them from its terminals.
    area2 = client.post("/api/areas", json={"code": "2", "name": "Branch"}).json()
    client.put(f"/api/employees/{r.json()['id']}", json={"area_ids": [area2["id"]]})
    a.drain()
    assert "100" not in a.users


def test_template_enrolled_on_one_device_reaches_the_others(client, device_factory):
    a, b = device_factory("DEV_A"), device_factory("DEV_B")
    a.handshake(); b.handshake(); a.drain(); b.drain()
    a.enroll("200", "Omar Saleh", bio_type=9)
    a.enroll("200", "Omar Saleh", bio_type=1, no=6)
    emp = client.get("/api/employees", params={"q": "200"}).json()["rows"][0]
    assert emp["name"] == "Omar Saleh" and emp["face_count"] == 1 and emp["fp_count"] == 1
    b.drain()
    assert ("200", 9, 0) in b.templates and ("200", 1, 6) in b.templates
    # ...but not echoed back to the device it came from.
    a.drain()
    assert not any(c.startswith("DATA UPDATE BIODATA") for c in a.executed)


def test_incompatible_face_algorithm_gets_photo_instead(client, device_factory):
    a = device_factory("NEWFACE", face_ver="58")
    b = device_factory("OLDFACE", face_ver="39")
    a.handshake(); b.handshake(); a.drain(); b.drain()
    a.enroll("300", "Mona", bio_type=9)
    # the terminal also uploads the enrollment photo
    client.post("/iclock/cdata", params={"SN": "NEWFACE", "table": "BIOPHOTO"},
                content=b"BIOPHOTO PIN=300\tFileName=300.jpg\tType=9\tSize=4\tContent=/9j/AAAA")
    b.drain()
    assert ("300", 9, 0) not in b.templates
    assert b.photos.get("300") == "/9j/AAAA"


def test_commands_and_results(client, device_factory):
    dev = device_factory("CMD1")
    dev.handshake(); dev.drain()
    d = _dev(client, "CMD1")
    for act in ("reboot", "sync_time", "info"):
        assert client.post(f"/api/devices/{d['id']}/action", json={"action": act}).json()["queued"] == 1
    dev.drain()
    assert dev.reboots == 1
    cmds = client.get("/api/device-commands", params={"sn": "CMD1"}).json()["rows"]
    assert cmds and all(c["status"] == "done" for c in cmds)


def test_upload_attendance_query(client, device_factory):
    dev = device_factory("Q1")
    dev.handshake(); dev.drain()
    base = datetime.now().replace(microsecond=0) - timedelta(days=2)
    for i in range(5):
        dev.punch("9", base + timedelta(minutes=i * 10), upload=False)
    d = _dev(client, "Q1")
    client.post(f"/api/devices/{d['id']}/action", json={"action": "upload_att"})
    dev.drain()
    assert client.get("/api/transactions", params={"sn": "Q1"}).json()["total"] == 5


def test_pull_users_from_device(client, device_factory):
    dev = device_factory("U1")
    dev.handshake(); dev.drain()
    dev.users["555"] = {"pin": "555", "name": "Device Only", "card": "777", "pri": "0", "passwd": ""}
    d = _dev(client, "U1")
    client.post(f"/api/devices/{d['id']}/action", json={"action": "upload_users"})
    dev.drain()
    emp = client.get("/api/employees", params={"q": "555"}).json()["rows"][0]
    assert emp["name"] == "Device Only" and emp["card_no"] == "777"


def test_resign_removes_from_devices(client, device_factory):
    dev = device_factory("R1")
    dev.handshake()
    e = client.post("/api/employees", json={"emp_code": "400", "first_name": "Leaving"}).json()
    dev.drain()
    assert "400" in dev.users
    client.post("/api/employees/batch", json={"ids": [e["id"]], "action": "resign", "resign_date": "2026-01-01"})
    dev.drain()
    assert "400" not in dev.users


def test_unknown_device_blocked_when_auto_add_off(client):
    client.put("/api/settings", json={"adms.auto_add": False})
    r = client.get("/iclock/cdata", params={"SN": "STRANGER", "options": "all"})
    assert r.text == "UNKNOWN DEVICE"
    assert client.get("/iclock/getrequest", params={"SN": "STRANGER"}).text == "OK"


def test_attphoto_and_monitor(client, device_factory, monkeypatch):
    import hader.api.devices as device_api
    monkeypatch.setattr(device_api, "now", lambda: datetime(2026, 9, 2, 12))
    client.put("/api/settings", json={"adms.upload_photos": True})  # punch photos are off by default
    dev = device_factory("P1")
    dev.handshake()
    t = datetime(2026, 9, 2, 8, 0, 0)
    dev.punch("11", t)
    client.post("/iclock/cdata", params={"SN": "P1", "table": "ATTPHOTO"},
                content=b"PIN=20260902080000-11.jpg\nSN=P1\nsize=4\nCMD=uploadphoto\x00\xff\xd8\xff\xd9")
    mon = client.get("/api/monitor").json()
    row = mon["rows"][-1]
    assert row["emp_code"] == "11" and row["has_photo"]
    assert client.get(f"/api/transactions/{row['id']}/photo").content == b"\xff\xd8\xff\xd9"


def test_rtdata_time(client):
    r = client.get("/iclock/rtdata", params={"SN": "T1", "type": "time"})
    assert r.text.startswith("DateTime=") and "ServerTZ=" in r.text


def test_push3_tabledata_upload(client):
    client.get("/iclock/cdata", params={"SN": "P3", "options": "all"})
    body = ("user uid=1\tcardno=4455\tpin=808\tpassword=\tgroup=1\tstarttime=0\tendtime=0\tname=Khaled\tprivilege=0\n"
            "biodata pin=808\tno=0\tindex=0\tvalid=1\tduress=0\ttype=9\tmajorver=58\tminorver=1\tformat=0\ttmp=QUFB\n")
    r = client.post("/iclock/cdata", params={"SN": "P3", "table": "tabledata", "tablename": "user", "count": 2},
                    content=body.encode())
    assert r.text == "user=2"
    emp = client.get("/api/employees", params={"q": "808"}).json()["rows"][0]
    assert emp["name"] == "Khaled" and emp["card_no"] == "4455" and emp["face_count"] == 1


def test_last_sync_and_transferring_state(client, device_factory):
    client.put("/api/settings", json={"adms.read_bio_on_punch": True})
    dev = device_factory("SYNC1")
    dev.handshake()
    d = _dev(client, "SYNC1")
    assert d["last_sync"] is None  # the options upload alone is not a data sync
    client.post("/api/employees", json={"emp_code": "61", "first_name": "Queued"})
    assert _dev(client, "SYNC1")["transferring"] is True
    dev.drain()
    dev.punch("61")
    # 61 has no picture or biometrics yet: the terminal is asked for that person's record
    cmds = [c["content"] for c in client.get("/api/device-commands", params={"sn": "SYNC1", "status": "pending"}).json()["rows"]]
    assert any("Pin=61" in c for c in cmds)
    dev.drain()
    d = _dev(client, "SYNC1")
    assert d["transferring"] is False and d["last_sync"]
    dev.punch("61")   # asked once only
    assert _dev(client, "SYNC1")["transferring"] is False


def test_old_database_gets_new_columns(client):
    from sqlalchemy import inspect, text
    from hader.bootstrap import init_db
    from hader.db import engine
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE device DROP COLUMN last_sync'))
    assert "last_sync" not in {c["name"] for c in inspect(engine).get_columns("device")}
    init_db()
    assert "last_sync" in {c["name"] for c in inspect(engine).get_columns("device")}


def test_terminal_photos_are_not_kept_by_default(client, device_factory):
    dev = device_factory(sn="NOPHOTO")
    dev.handshake()
    r = client.post("/iclock/cdata", params={"SN": "NOPHOTO", "table": "ATTPHOTO", "Stamp": "1"},
                    content=b"PIN=20261007080000-5.jpg\nSN=NOPHOTO\nsize=4\nCMD=uploadphoto\x00\xff\xd8\xff\xd9")
    assert r.status_code == 200 and r.text.startswith("OK")
    from hader.config import settings
    assert not list((settings.photos_dir / "NOPHOTO").glob("*")) if (settings.photos_dir / "NOPHOTO").exists() else True


def test_pull_everyone_asks_every_terminal_and_counts_missing_people(client, device_factory):
    dev = device_factory("EVERY1")
    dev.handshake()
    dev.punch("77")   # unknown person: created from the punch, no name yet
    r = client.post("/api/devices/pull-everyone").json()
    assert r["push"] == 1 and r["direct"] == 0
    assert r["missing"]["punched"] >= 1 and r["missing"]["no_name"] >= 1
    tables = {c["content"].split("tablename=")[1].split(",")[0]
              for c in client.get("/api/device-commands", params={"sn": "EVERY1"}).json()["rows"] if "tablename=" in c["content"]}
    assert {"user", "biophoto", "userpic"} <= tables
    assert client.get("/api/device-missing-people").json()["punched"] >= 1
