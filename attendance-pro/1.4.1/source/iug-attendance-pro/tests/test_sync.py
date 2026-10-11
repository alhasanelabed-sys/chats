"""Two-way synchronisation between the server and several terminals (area model).

The simulator is strict like real firmware: a template or photo for a PIN the
terminal does not know is rejected, so these tests also prove command ordering.
"""
from datetime import datetime


def _dev(client, sn):
    return next(d for d in client.get("/api/devices").json()["rows"] if d["sn"] == sn)


def _pending(client, sn):
    return [c for c in client.get("/api/device-commands", params={"sn": sn}).json()["rows"]
            if c["status"] in ("pending", "sent")]


def _settle(*devices, rounds=10):
    for _ in range(rounds):
        if not sum(d.drain() for d in devices):
            return


def test_user_enrolled_on_one_terminal_reaches_the_others(client, device_factory):
    a, b, c = (device_factory(sn) for sn in ("A1", "B1", "C1"))
    for d in (a, b, c):
        d.handshake()
    _settle(a, b, c)
    a.enroll("700", "Rami Nasser", bio_type=9)
    a.enroll("700", "Rami Nasser", bio_type=1, no=3)
    _settle(a, b, c)
    for d in (b, c):
        assert d.users["700"]["name"] == "Rami Nasser"
        assert ("700", 9, 0) in d.templates and ("700", 1, 3) in d.templates
    cmds = client.get("/api/device-commands", params={"sn": "B1"}).json()["rows"]
    assert all(x["status"] == "done" for x in cmds), [x for x in cmds if x["status"] != "done"]


def test_card_changed_on_terminal_propagates(client, device_factory):
    a, b = device_factory("A1"), device_factory("B1")
    a.handshake(); b.handshake()
    client.post("/api/employees", json={"emp_code": "701", "first_name": "Huda"})
    _settle(a, b)
    a.edit_user_on_device("701", card="99887766", passwd="1234")
    _settle(a, b)
    emp = client.get("/api/employees", params={"q": "701"}).json()["rows"][0]
    assert emp["card_no"] == "99887766" and "dev_password" not in emp
    from hader import models as m
    from hader.db import session_scope
    with session_scope() as db:
        assert db.query(m.Employee).filter_by(emp_code="701").one().dev_password == "1234"
    assert b.users["701"]["card"] == "99887766"
    assert emp["name"] == "Huda"  # the server stays master for names


def test_echoing_firmware_does_not_loop(client, device_factory):
    a, b = device_factory("A1", echo=True), device_factory("B1", echo=True)
    a.handshake(); b.handshake()
    client.post("/api/employees", json={"emp_code": "702", "first_name": "Loop", "card_no": "5"})
    a.enroll("703", "Echo Test", bio_type=9)
    _settle(a, b, rounds=20)
    assert not _pending(client, "A1") and not _pending(client, "B1")
    assert "702" in a.users and "702" in b.users and "703" in b.users


def test_userpic_from_terminal_propagates(client, device_factory):
    a, b = device_factory("A1"), device_factory("B1")
    a.handshake(); b.handshake()
    a.enroll("704", "Pic", bio_type=9)
    client.post("/iclock/cdata", params={"SN": "A1", "table": "OPERLOG"},
                content=b"USERPIC PIN=704\tFileName=704.jpg\tSize=8\tContent=/9j/PIC=")
    _settle(a, b)
    assert b.photos.get("704") == "/9j/PIC="
    emp = client.get("/api/employees", params={"q": "704"}).json()["rows"][0]
    assert emp["has_photo"]


def test_unknown_terminal_keeps_its_data_until_approved(client):
    client.put("/api/settings", json={"adms.auto_add": False})
    body = b"9\t2026-09-01 08:00:00\t0\t15\t0\t0\t0"
    r = client.post("/iclock/cdata", params={"SN": "NEWDEV", "table": "ATTLOG"}, content=body)
    assert r.status_code == 403  # not "OK": the terminal must keep the record and retry
    assert client.get("/api/transactions", params={"sn": "NEWDEV"}).json()["total"] == 0
    assert client.post("/api/devices", json={"sn": "NEWDEV", "alias": "New", "area_id": 1}).status_code == 200
    r = client.post("/iclock/cdata", params={"SN": "NEWDEV", "table": "ATTLOG"}, content=body)
    assert r.status_code == 200 and r.text == "OK: 1"
    assert client.get("/api/transactions", params={"sn": "NEWDEV"}).json()["total"] == 1


def test_disabled_terminal_is_refused(client, device_factory):
    a = device_factory("DIS1")
    a.handshake()
    d = _dev(client, "DIS1")
    client.put(f"/api/devices/{d['id']}", json={"enabled": False})
    r = client.post("/iclock/cdata", params={"SN": "DIS1", "table": "ATTLOG"},
                    content=b"1\t2026-09-01 08:00:00\t0\t15")
    assert r.status_code == 403


def test_old_iclock_aspx_paths(client, device_factory):
    old = device_factory("ASPX1", suffix=".aspx")
    assert old.handshake()["GET OPTION FROM"] == "ASPX1"
    old.punch("12", datetime(2026, 9, 1, 8, 0))
    client.post("/api/employees", json={"emp_code": "705", "first_name": "Old"})
    old.drain()
    assert "705" in old.users
    assert client.get("/api/transactions", params={"sn": "ASPX1"}).json()["total"] == 1


def test_legacy_firmware_gets_fingertmp(client, device_factory):
    new, old = device_factory("NEW1"), device_factory("OLD1", legacy=True)
    new.handshake(); old.handshake()
    _settle(new, old)
    new.enroll("706", "Finger", bio_type=1, no=2)
    _settle(new, old)
    assert ("706", 1, 2) in old.templates
    assert not any(c.startswith("DATA UPDATE BIODATA") for c in old.executed)
    emp = client.get("/api/employees", params={"q": "706"}).json()["rows"][0]
    tpl = client.get(f"/api/employees/{emp['id']}").json()["templates"][0]
    client.delete(f"/api/employees/{emp['id']}/templates/{tpl['id']}")
    _settle(new, old)
    assert ("706", 1, 2) not in old.templates and ("706", 1, 2) not in new.templates


def test_bad_line_does_not_lose_the_batch(client, device_factory):
    a = device_factory("BAD1")
    a.handshake()
    body = ("USER PIN=801\tName=Good One\tPri=0\tPasswd=\tCard=\n"
            "BIODATA Pin=801\tNo=x\tIndex=0\tValid=1\tDuress=0\tType=9\tMajorVer=39\tTmp=AAAA\n"
            "USER PIN=802\tName=Good Two\tPri=0\tPasswd=\tCard=\n")
    r = client.post("/iclock/cdata", params={"SN": "BAD1", "table": "OPERLOG"}, content=body.encode())
    assert r.status_code == 200 and r.text == "OK: 3"
    names = {e["name"] for e in client.get("/api/employees", params={"q": "80"}).json()["rows"]}
    assert {"Good One", "Good Two"} <= names


def test_punches_relink_when_employee_is_recreated(client, device_factory):
    a = device_factory("RL1")
    a.handshake()
    a.punch("900", datetime(2026, 9, 1, 8, 0))
    emp = client.get("/api/employees", params={"q": "900"}).json()["rows"][0]
    client.delete(f"/api/employees/{emp['id']}")
    new = client.post("/api/employees", json={"emp_code": "900", "first_name": "Back"}).json()
    rows = client.get("/api/attendance/daily", params={"start": "2026-09-01", "end": "2026-09-01",
                                                       "employee_ids": str(new["id"])}).json()["rows"]
    assert rows and rows[0]["punches"] == ["08:00"]


def test_attendance_flag_does_not_remove_user_from_terminals(client, device_factory):
    a = device_factory("EA1")
    a.handshake()
    e = client.post("/api/employees", json={"emp_code": "901", "first_name": "Manager", "enable_att": False}).json()
    a.drain()
    assert "901" in a.users
    client.put(f"/api/employees/{e['id']}", json={"enable_att": False, "card_no": "7"})
    a.drain()
    assert a.users["901"]["card"] == "7"


def test_new_terminal_is_asked_for_everything(client):
    text = client.get("/iclock/cdata", params={"SN": "FRESH", "options": "all"}).text
    assert "ATTLOGStamp=None" in text and "OPERLOGStamp=None" in text


def test_counters_refreshed_when_firmware_sends_no_info(client):
    client.get("/iclock/cdata", params={"SN": "NOINFO", "options": "all"})
    reply = client.get("/iclock/getrequest", params={"SN": "NOINFO"}).text
    assert any(line.endswith(":INFO") for line in reply.splitlines())
    cid = next(line.split(":")[1] for line in reply.splitlines() if line.endswith(":INFO"))
    client.post("/iclock/devicecmd", params={"SN": "NOINFO"},
                content=f"ID={cid}&Return=0&CMD=INFO\n~DeviceName=SpeedFace-V5L\nUserCount=420\nFaceCount=399\n"
                        f"FPCount=246\nPvCount=80\nTransactionCount=20941".encode())
    d = _dev(client, "NOINFO")
    assert (d["user_count"], d["face_count"], d["fp_count"], d["palm_count"], d["att_count"]) == (420, 399, 246, 80, 20941)
    # not asked again on the next heartbeat
    assert ":INFO" not in client.get("/iclock/getrequest", params={"SN": "NOINFO"}).text


def test_remote_enrollment_end_to_end(client, device_factory):
    """Remote registration: the server asks terminal A to enroll a palm; A uploads it,
    the palm goes to terminal B of the same area, and the progress endpoint shows each step."""
    a, b = device_factory(sn="ENR-A"), device_factory(sn="ENR-B")
    a.handshake()
    b.handshake()
    dept = client.get("/api/departments").json()["rows"][0]["id"]
    emp = client.post("/api/employees", json={"emp_code": "321", "first_name": "Rami", "department_id": dept,
                                              "area_ids": [1]}).json()
    _settle(a, b)
    r = client.post(f"/api/employees/{emp['id']}/enroll",
                    json={"device_id": _dev(client, "ENR-A")["id"], "bio_type": 8}).json()
    st = client.get(f"/api/enroll/{r['cmd_id']}").json()
    assert st["status"] == "pending" and st["received"] == 0
    _settle(a, b)
    st = client.get(f"/api/enroll/{r['cmd_id']}").json()
    assert st["status"] == "done" and st["received"] == 1
    assert st["distributed"] == 1 and st["delivered"] == 1
    assert ("321", 8, 0) in b.templates
    rows = client.get(f"/api/employees/{emp['id']}/devices").json()["rows"]
    assert {r["sn"] for r in rows} == {"ENR-A", "ENR-B"} and all(r["pending"] == 0 for r in rows)


def test_remote_enrollment_refused_on_offline_or_direct_terminal(client):
    from hader.db import session_scope
    from hader import models as m
    with session_scope() as db:
        db.add(m.Device(sn="DIRECT", alias="D", ip="10.0.0.9", area_id=1, managed_by="tcp"))
        db.add(m.Device(sn="OFF", alias="O", ip="10.0.0.8", area_id=1))
    dept = client.get("/api/departments").json()["rows"][0]["id"]
    emp = client.post("/api/employees", json={"emp_code": "322", "first_name": "X", "department_id": dept}).json()
    for sn in ("DIRECT", "OFF"):
        r = client.post(f"/api/employees/{emp['id']}/enroll", json={"device_id": _dev(client, sn)["id"], "bio_type": 1})
        assert r.status_code == 409


def test_remote_pull_of_fingerprints_palms_faces_and_photos(client, device_factory):
    """Templates enrolled on a terminal that never reached the server are fetched on request:
    for the whole terminal, or for one person from every terminal of their areas."""
    a = device_factory(sn="PULL-A")
    a.handshake()
    a.users["500"] = {"pin": "500", "name": "Nour", "pri": "0", "passwd": "", "card": ""}
    a.users["501"] = {"pin": "501", "name": "Hala", "pri": "0", "passwd": "", "card": ""}
    for pin in ("500", "501"):
        a.enroll(pin, bio_type=1, no=6, upload=False)
        a.enroll(pin, bio_type=8, upload=False)
        a.enroll(pin, bio_type=9, upload=False)
    dev = _dev(client, "PULL-A")
    r = client.post(f"/api/devices/{dev['id']}/action", json={"action": "pull_bio"}).json()
    assert r["queued"] >= 3  # the user list may already be asked for (new terminal)
    assert client.get(f"/api/devices/{dev['id']}/bio-status").json()["pending"] == 4
    _settle(a)
    st = {x["key"]: x["server"] for x in client.get(f"/api/devices/{dev['id']}/bio-status").json()["rows"]}
    assert st == {"fp": 2, "face": 2, "palm": 2, "photo": 2}
    emp = client.get("/api/employees", params={"q": "500"}).json()["rows"][0]
    assert emp["name"] == "Nour"
    img = client.get(f"/api/employees/{emp['id']}/biophoto")
    assert img.status_code == 200 and img.content[:3] == b"\xff\xd8\xff"
    # one person, later: a new finger enrolled at the keypad is fetched on request
    a.enroll("501", bio_type=1, no=2, upload=False)
    e2 = client.get("/api/employees", params={"q": "501"}).json()["rows"][0]
    assert client.post(f"/api/employees/{e2['id']}/pull-bio").json()["queued"] == 4
    _settle(a)
    full = client.get(f"/api/employees/{e2['id']}").json()
    assert {(t["bio_type"], t["no"]) for t in full["templates"]} >= {(1, 6), (1, 2), (8, 0), (9, 0)}
