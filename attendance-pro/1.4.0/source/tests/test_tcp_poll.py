"""Direct link over port 4370 (our own protocol code) against a simulated terminal."""
from datetime import datetime

import pytest

from hader import tcp_pull
from terminal_simulator import FakeTerminal, serve


@pytest.fixture()
def terminal():
    term = FakeTerminal(sn="TERM4370A", comm_key=0).seed(3, 5)
    port, stop = serve(term)
    yield term, port
    stop()


def _dev(client, sn):
    return next(x for x in client.get("/api/devices").json()["rows"] if x["sn"] == sn)


def test_discovery_registers_terminal_and_reads_everything(client, terminal):
    term, port = terminal
    r = client.post("/api/devices/discover", json={"networks": "127.0.0.1", "port": port}).json()
    assert [f["sn"] for f in r["found"]] == ["TERM4370A"] and r["found"][0]["new"] is True
    d = _dev(client, "TERM4370A")
    assert d["managed_by"] == "tcp" and d["state"] == "online" and d["model"] == "SpeedFace-V5L"
    assert d["user_count"] == 3 and d["fp_alg"] == "10"
    assert client.get("/api/transactions", params={"sn": "TERM4370A"}).json()["total"] == 5
    emp = client.get("/api/employees", params={"q": "101"}).json()["rows"][0]
    assert emp["name"] == "User 1" and emp["card_no"] == "5001"
    from hader.db import session_scope
    from hader import models as m
    with session_scope() as db:
        kinds = sorted((t.bio_type, t.bio_no) for t in db.query(m.BioTemplate).all())
    assert kinds.count((1, 0)) == 3 and kinds.count((2, 0)) == 3  # fingers + near-infrared faces
    # scanning again finds the same terminal, creates nothing new
    again = client.post("/api/devices/discover", json={"networks": "127.0.0.1", "port": port}).json()
    assert again["found"][0]["new"] is False
    assert len(client.get("/api/devices").json()["rows"]) == 1


def test_comm_key_is_found_from_the_settings_list(client):
    term = FakeTerminal(sn="KEYED", comm_key=4321).seed(1, 1)
    port, stop = serve(term)
    try:
        client.put("/api/settings", json={"discovery.comm_keys": "1,4321"})
        r = client.post("/api/devices/discover", json={"networks": "127.0.0.1", "port": port}).json()
        assert r["found"][0]["ok"] is True
        assert client.get("/api/transactions", params={"sn": "KEYED"}).json()["total"] == 1
    finally:
        stop()


def test_employee_changes_are_written_to_direct_terminal(client, terminal):
    term, port = terminal
    client.put("/api/settings", json={"tcp.write_back": True})
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    dept = client.get("/api/departments").json()["rows"][0]["id"]
    client.post("/api/employees", json={"emp_code": "900", "first_name": "Omar", "department_id": dept,
                                        "card_no": "4242", "area_ids": [1]})
    tcp_pull.poll_due({})
    u = next(u for u in term.users if u.user_id == "900")
    assert u.name.startswith("Omar") and u.card == 4242
    # a fingerprint stored here goes to the terminal too
    import base64
    from hader.adms import commands as C
    from hader.adms import sync
    from hader.db import session_scope
    from hader import models as m
    with session_scope() as db:
        emp = db.query(m.Employee).filter_by(emp_code="900").one()
        dev = db.query(m.Device).filter_by(sn="TERM4370A").one()
        tpl = m.BioTemplate(employee_id=emp.id, bio_type=1, bio_no=6, major_ver="10",
                            template=base64.b64encode(b"\x07" * 400).decode())
        db.add(tpl)
        db.flush()
        sync.queue(db, dev.sn, C.fingertmp_update("900", tpl))
    tcp_pull.deliver_pending("TERM4370A")
    assert any(t.uid == u.uid and t.fid == 6 and t.template == b"\x07" * 400 for t in term.templates)
    # resignation removes the user from the terminal
    eid = client.get("/api/employees", params={"q": "900"}).json()["rows"][0]["id"]
    client.delete(f"/api/employees/{eid}")
    tcp_pull.poll_due({})
    assert not any(u.user_id == "900" for u in term.users)


@pytest.fixture()
def other_program_holds_port_90(client, monkeypatch):
    from hader.adms import ports
    monkeypatch.setattr(ports, "PORTS", {8090: {"port": 8090, "mode": "own", "detail": ""},
                                         90: {"port": 90, "mode": "waiting", "detail": "in use"}})
    # Port ownership is diagnostic. Only the operator's explicit setting
    # authorizes writes; the normal workflow fixture opts in by default.
    assert client.put("/api/settings", json={"tcp.write_back": False}).status_code == 200


def test_read_only_while_other_program_holds_port_90(client, terminal, other_program_holds_port_90):
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    dept = client.get("/api/departments").json()["rows"][0]["id"]
    client.post("/api/employees", json={"emp_code": "901", "first_name": "Sami", "department_id": dept,
                                        "area_ids": [1]})
    tcp_pull.poll_due({})
    assert not any(u.user_id == "901" for u in term.users)
    d = _dev(client, "TERM4370A")
    assert d["state"] == "online" and d["transferring"] is False and d["pending"] == 0
    assert client.get("/api/link-mode").json() == {"full": False, "writing": False, "port": 90,
                                                   "mode": "waiting", "detail": "in use"}
    r = client.post(f"/api/devices/{d['id']}/action", json={"action": "reboot"})
    assert r.status_code == 409
    # reading still works
    assert client.post(f"/api/devices/{d['id']}/pull").status_code == 200


def test_full_mode_when_port_90_is_ours(client, terminal, monkeypatch):
    from hader.adms import ports
    monkeypatch.setattr(ports, "PORTS", {90: {"port": 90, "mode": "own", "detail": ""}})
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    dept = client.get("/api/departments").json()["rows"][0]["id"]
    client.post("/api/employees", json={"emp_code": "902", "first_name": "Huda", "department_id": dept,
                                        "area_ids": [1]})
    tcp_pull.poll_due({})
    assert any(u.user_id == "902" for u in term.users)
    assert client.get("/api/link-mode").json()["full"] is True


def test_actions_run_immediately_on_direct_terminal(client, terminal):
    term, port = terminal
    dev = client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port}).json()
    d = _dev(client, dev["sn"])
    r = client.post(f"/api/devices/{d['id']}/action", json={"action": "sync_time"}).json()
    assert r["delivered"]["done"] == 1
    assert abs((term.clock - datetime.now()).total_seconds()) < 5
    r = client.post(f"/api/devices/{d['id']}/action", json={"action": "clear_log"}).json()
    assert r["delivered"]["done"] == 1 and term.punches == []


def test_point_terminal_to_this_server(client, terminal):
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    d = _dev(client, "TERM4370A")
    cur = client.get(f"/api/devices/{d['id']}/server").json()
    assert cur["current"]["WebServerIP"] == "10.0.0.5" and cur["current"]["WebServerPort"] == "90"
    r = client.post(f"/api/devices/{d['id']}/server", json={"ip": "10.0.0.34", "port": 8081, "reboot": False}).json()
    assert r["device_settings"]["WebServerIP"] == "10.0.0.34"
    assert term.options["WebServerPort"] == "8081" and term.options["ICLOCKSVRURL"] == "http://10.0.0.34:8081"


def test_terminal_leaves_direct_mode_when_it_pushes(client, terminal, device_factory):
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    device_factory(sn="TERM4370A").handshake()
    assert _dev(client, "TERM4370A")["managed_by"] is None


def test_every_minute_counters_refresh_and_only_changes_are_read(client, terminal):
    from datetime import timedelta
    from hader.terminal import protocol as P
    term, port = terminal
    term.options["~PvCount"] = "82"
    client.post("/api/devices", json={"sn": "TERM4370A", "alias": "A", "area_id": 1, "ip": "127.0.0.1",
                                      "tcp_port": port, "tcp_poll": True})
    client.post("/api/devices", json={"sn": "B", "alias": "B", "area_id": 1, "ip": "127.0.0.9",
                                      "tcp_port": 1})  # not ticked: never contacted
    state = {}
    tcp_pull.poll_due(state)
    d = _dev(client, "TERM4370A")
    assert (d["user_count"], d["fp_count"], d["face_count"], d["palm_count"], d["att_count"]) == (3, 3, 3, 82, 5)
    assert d["state"] == "online"
    assert client.get("/api/transactions", params={"sn": "TERM4370A"}).json()["total"] == 5
    # nothing changed: the next minute is only a status check (no table download)
    n = len(term.commands)
    tcp_pull.poll_due(state)
    assert P.CMD_DATA_WRRQ not in term.commands[n:]
    # an employee punches: the next minute brings the punch and the new counter
    u = term.users[0]
    term.punches.append(P.DevPunch(u.user_id, term.punches[-1].time + timedelta(hours=1), 1, 15, uid=u.uid))
    tcp_pull.poll_due(state)
    assert _dev(client, "TERM4370A")["att_count"] == 6
    assert client.get("/api/transactions", params={"sn": "TERM4370A"}).json()["total"] == 6
    assert client.get("/api/tcp-status").json()["TERM4370A"]["ok"] is True


def test_switched_off_terminal_goes_offline(client, terminal):
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    from hader.db import session_scope
    from hader import models as m
    with session_scope() as db:
        d = db.query(m.Device).filter_by(sn="TERM4370A").one()
        d.tcp_port = 1  # nothing answers there any more
        d.last_activity = d.last_activity.replace(year=2020)
    tcp_pull.poll_due({})
    assert _dev(client, "TERM4370A")["state"] == "offline"
    assert client.get("/api/tcp-status").json()["TERM4370A"]["ok"] is False


def test_unreachable_device_is_reported(client):
    d = client.post("/api/devices", json={"sn": "X", "alias": "X", "area_id": 1, "ip": "127.0.0.1",
                                          "tcp_port": 1}).json()
    r = client.post(f"/api/devices/{d['id']}/pull")
    assert r.status_code == 502
    assert client.get("/api/tcp-status").json()["X"]["ok"] is False


def test_terminals_talking_to_this_pc_are_found_without_any_address(client, terminal, monkeypatch):
    """A terminal pushing to port 90 of this PC appears by itself."""
    from hader.terminal import discovery
    term, port = terminal
    monkeypatch.setattr(discovery, "connected_peers", lambda ports: ["127.0.0.1"])
    monkeypatch.setattr(discovery, "scan", lambda nets, port: [])
    r = tcp_pull.discover(port=port, peers_only=True)
    assert r["found"][0]["sn"] == "TERM4370A"
    assert _dev(client, "TERM4370A")["user_count"] == 3


def test_netstat_parsing(monkeypatch):
    import subprocess
    from hader.terminal import discovery
    out = ("  Proto  Local Address          Foreign Address        State\n"
           "  TCP    0.0.0.0:90             0.0.0.0:0              LISTENING\n"
           "  TCP    10.0.0.34:90           10.28.64.18:50211      ESTABLISHED\n"
           "  TCP    10.0.0.34:90           10.66.0.101:49152      TIME_WAIT\n"
           "  TCP    10.0.0.34:443          10.9.9.9:5000          ESTABLISHED\n")
    monkeypatch.setattr(discovery.os.path, "exists", lambda p: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    assert discovery.connected_peers((90,)) == ["10.28.64.18", "10.66.0.101"]


def test_area_sync_between_direct_terminals(client, monkeypatch):
    """Full mode: a user enrolled on terminal A (with fingerprints) reaches terminal B of the
    same area, and B's own users reach A (area sync)."""
    from hader.adms import ports
    from hader.terminal import protocol as P
    monkeypatch.setattr(ports, "PORTS", {90: {"port": 90, "mode": "own", "detail": ""}})
    a = FakeTerminal(sn="AREA-A").seed(2, 2)
    b = FakeTerminal(sn="AREA-B")
    b.users.append(P.DevUser(uid=1, user_id="700", name="Only on B", card=7))
    b.templates.append(P.DevTemplate(uid=1, fid=3, valid=1, template=b"\x09" * 300))
    pa, stop_a = serve(a)
    pb, stop_b = serve(b)
    try:
        for p in (pa, pb):
            client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": p})
        state = {}
        tcp_pull.poll_due(state)   # read both, queue what each one misses
        tcp_pull.poll_due(state)   # deliver
        assert {u.user_id for u in b.users} == {"101", "102", "700"}
        assert {u.user_id for u in a.users} == {"101", "102", "700"}
        b_uid = {u.user_id: u.uid for u in b.users}
        assert any(t.uid == b_uid["101"] and t.fid == 0 for t in b.templates)
        a_uid = {u.user_id: u.uid for u in a.users}
        assert any(t.uid == a_uid["700"] and t.fid == 3 and t.template == b"\x09" * 300 for t in a.templates)
        # faces / photos are not attempted over 4370, so no failed commands pile up
        assert client.get("/api/device-commands", params={"status": "failed"}).json()["total"] == 0
        # once in sync, nothing more is queued
        tcp_pull.poll_due({})
        assert client.get("/api/device-commands", params={"status": "pending"}).json()["total"] == 0
    finally:
        stop_a()
        stop_b()


def test_invalid_legacy_pins_are_retained_for_review_and_missed_punches_recovered(client, terminal):
    from hader.bootstrap import purge_invalid_people
    from hader.db import session_scope
    from hader import models as m
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    with session_scope() as db:
        e = m.Employee(emp_code="┌3172", first_name="")
        db.add(e)
        db.flush()
        db.add(m.Transaction(emp_code=e.emp_code, employee_id=e.id, punch_time=datetime(2026, 10, 4, 7, 0),
                             device_sn="TERM4370A"))
        # a real punch that the faulty version had missed
        db.query(m.Transaction).filter_by(emp_code="103").delete()
    assert purge_invalid_people() == 1
    codes = [r["emp_code"] for r in client.get("/api/employees").json()["rows"]]
    assert "┌3172" in codes  # invalidity is not proof that stored history can be deleted
    from hader import store
    with session_scope() as db:
        report = store.get(db, "data.invalid_pins")
        assert report["review_required"] and report["retained"]
        assert "┌3172" in report["code_sample"]
    tcp_pull.read_and_store("TERM4370A")   # whole log compared once: the missed punch comes back
    rows = client.get("/api/transactions", params={"sn": "TERM4370A"}).json()["rows"]
    assert {r["emp_code"] for r in rows} == {"101", "102", "103", "┌3172"}


def test_arabic_names_read_from_terminal_and_completed_later(client, terminal):
    term, port = terminal
    term.users[0].name = "محمد زكريا محمد المدهون"   # longer than the 24-byte field
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    emp = client.get("/api/employees", params={"q": "101"}).json()["rows"][0]
    assert emp["name"] and "محمد زكريا محمد المدهون".startswith(emp["name"])
    assert all(not ("\u0080" <= ch <= "ÿ") for ch in emp["name"])
    # the full name (e.g. from a terminal pushing over port 90) completes the shortened one
    from hader.adms import sync
    from hader.db import session_scope
    with session_scope() as db:
        sync._apply_user(db, None, {"pin": "101", "name": "محمد زكريا محمد المدهون"})
    assert client.get("/api/employees", params={"q": "101"}).json()["rows"][0]["name"] == "محمد زكريا محمد المدهون"


def test_pull_bio_on_direct_terminal_in_read_only_mode_only_reads(client, terminal, other_program_holds_port_90):
    term, port = terminal
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    d = _dev(client, "TERM4370A")
    n = len(term.users)
    r = client.post(f"/api/devices/{d['id']}/action", json={"action": "pull_bio"}).json()
    assert "delivered" not in r and r["read"]["templates"] == 6   # 3 fingers + 3 near-infrared faces
    assert len(term.users) == n
    st = {x["key"]: x["server"] for x in client.get(f"/api/devices/{d['id']}/bio-status").json()["rows"]}
    assert st["fp"] == 3 and st["face"] == 3


def test_remote_device_panel_reads_and_writes_terminal_settings(client, terminal, monkeypatch):
    from hader.adms import ports
    term, port = terminal
    term.options.update({"VOLUME": "40", "IdleMinute": "5", "IPAddress": "10.28.64.18", "SomeVendorKey": "7"})
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    d = _dev(client, "TERM4370A")
    p = client.get(f"/api/devices/{d['id']}/panel").json()
    assert p["live"] is True
    sec = {s["id"]: {f["key"]: f["value"] for f in s["fields"]} for s in p["sections"]}
    assert sec["info"]["SerialNumber"] == "TERM4370A" and sec["personal"]["VOLUME"] == "40"
    assert sec["server"]["WebServerPort"] == "90" and sec["network"]["IPAddress"] == "10.28.64.18"
    # options a terminal reported over port 90 that have no label are listed as they are
    from hader.adms import sync
    from hader.db import session_scope
    from hader import models as m
    with session_scope() as db:
        sync.apply_device_info(db.query(m.Device).filter_by(sn="TERM4370A").one(), {"SomeVendorKey": "7"})
    assert client.get(f"/api/devices/{d['id']}/panel").json()["advanced"]["SomeVendorKey"] == "7"
    # Explicit read-only mode applies while another program holds port 90.
    monkeypatch.setattr(ports, "PORTS", {90: {"port": 90, "mode": "waiting", "detail": ""}})
    client.put("/api/settings", json={"tcp.write_back": False})
    assert client.post(f"/api/devices/{d['id']}/panel", json={"options": {"VOLUME": "70"}}).status_code == 409
    monkeypatch.setattr(ports, "PORTS", {90: {"port": 90, "mode": "own", "detail": ""}})
    client.put("/api/settings", json={"tcp.write_back": True})
    r = client.post(f"/api/devices/{d['id']}/panel", json={"options": {"VOLUME": "70", "IdleMinute": "10"}}).json()
    assert r["applied"] == {"VOLUME": "70", "IdleMinute": "10"}
    assert term.options["VOLUME"] == "70"


def test_device_panel_opens_at_once_while_the_terminal_is_busy(client, terminal, monkeypatch):
    from hader import console, tcp_pull
    term, port = terminal
    term.options.update({"MThreshold": "35", "VOLUME": "40"})
    client.post("/api/devices/probe", json={"ip": "127.0.0.1", "port": port})
    d = _dev(client, "TERM4370A")
    p = client.get(f"/api/devices/{d['id']}/panel?live=false").json()
    assert p["live"] is False and p["busy"] is False and p["sn"] == "TERM4370A"
    live = client.get(f"/api/devices/{d['id']}/panel").json()
    assert {f["key"]: f["value"] for s in live["sections"] for f in s["fields"]}["MThreshold"] == "35"
    # a background read holds the terminal: the panel answers with saved values instead of hanging
    monkeypatch.setattr(console, "_lock", lambda sn, wait: None)
    busy = client.get(f"/api/devices/{d['id']}/panel").json()
    assert busy["busy"] is True and busy["live"] is False
    monkeypatch.undo()
    r = client.get(f"/api/devices/{d['id']}/panel/option", params={"key": "VOLUME"}).json()
    assert r == {"key": "VOLUME", "value": "40", "live": True}
    assert tcp_pull._lock("TERM4370A").acquire(timeout=1)   # nothing left locked
    tcp_pull._lock("TERM4370A").release()
