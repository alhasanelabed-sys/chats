"""Authentication, permissions, personnel import, settings, backup."""
from datetime import datetime
import io

from fastapi.testclient import TestClient

from hader.app import app


def test_login_required_and_wrong_password(client):
    anon = TestClient(app)
    assert anon.get("/api/employees").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code == 401


def test_role_permissions(client):
    viewer = next(r for r in client.get("/api/roles").json()["rows"] if r["name"] == "Viewer")
    r = client.post("/api/users", json={"username": "hr", "password": "viewer-secret1", "role_id": viewer["id"]})
    assert r.status_code == 200 and "password_hash" not in r.json()
    u = TestClient(app)
    assert u.post("/api/auth/login", json={"username": "hr", "password": "viewer-secret1"}).status_code == 200
    assert u.get("/api/employees").status_code == 200
    assert u.post("/api/employees", json={"emp_code": "9"}).status_code == 403
    assert u.get("/api/users").status_code == 403
    assert u.post("/api/devices", json={"sn": "X"}).status_code == 403


def test_password_change_invalidates_old_token(client):
    old = client.post("/api/auth/login", json={"username": "admin", "password": "admin-test-password"}).json()["token"]
    r = client.post("/api/auth/password", json={"old_password": "admin-test-password", "new_password": "new-test-password"})
    assert r.status_code == 200
    stale = TestClient(app)
    assert stale.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert client.get("/api/auth/me").json()["must_change_password"] is False


def test_password_hash_cannot_be_injected(client):
    u = client.post("/api/users", json={"username": "x1", "password": "viewer-secret1"}).json()
    client.put(f"/api/users/{u['id']}", json={"password_hash": "pbkdf2_sha256$1$00$00"})
    assert TestClient(app).post("/api/auth/login", json={"username": "x1", "password": "viewer-secret1"}).status_code == 200


def test_import_employees_csv(client):
    csv = "الرقم,الاسم,القسم,البطاقة\n301,Ali Hassan,Sales,555\n302,Mona,Sales,\n,bad,,\n"
    r = client.post("/api/employees/import", files={"file": ("e.csv", io.BytesIO(csv.encode("utf-8-sig")), "text/csv")})
    body = r.json()
    assert body["created"] == 2 and len(body["errors"]) == 1
    rows = client.get("/api/employees", params={"q": "30"}).json()["rows"]
    assert {e["department"] for e in rows} == {"Sales"} and rows[0]["card_no"] == "555"
    x = client.get("/api/employees-export", params={"fmt": "xlsx"})
    assert x.content[:2] == b"PK"


def test_duplicate_employee_code(client):
    assert client.post("/api/employees", json={"emp_code": "77"}).status_code == 200
    assert client.post("/api/employees", json={"emp_code": "77"}).status_code == 409
    assert client.post("/api/employees", json={"emp_code": "bad code!"}).status_code == 422


def test_settings_and_backup(client):
    s = client.put("/api/settings", json={"company.name": "ACME", "not.a.key": 1}).json()
    assert s["company.name"] == "ACME" and "not.a.key" not in s
    name = client.post("/api/backups").json()["name"]
    assert any(b["name"] == name for b in client.get("/api/backups").json()["rows"])
    downloaded = client.get(f"/api/backups/{name}")
    assert downloaded.content[:2] == b"PK" and name.endswith(".zip")
    assert client.get("/api/backups/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "core.js" in r.text
    assert client.get("/static/js/pages.js").status_code == 200


def test_dashboard(client, device_factory):
    dev = device_factory("DASH")
    dev.handshake()
    d = client.get("/api/dashboard").json()
    assert d["devices"] == 1 and d["online"] == 1 and len(d["trend"]) == 14
    assert len(d["hourly"]) == 24 and len(d["hourly_avg"]) == 24 and d["device_list"][0]["link"] == "ADMS"
    dev.punch("1", datetime.now().replace(minute=5, second=0, microsecond=0))
    from hader.api import system
    assert system._SNAP_DIRTY.is_set()      # the punch marked the ready copy as outdated
    system.refresh_dashboard()               # (the server does this in the background within ~3 s)
    d = client.get("/api/dashboard").json()
    assert d["hourly"][datetime.now().hour] == 1 and d["punches_today"] == 1


def test_live_events_stream(client, device_factory):
    from hader import events
    start = events.current()
    dev = device_factory("EVT1")
    dev.handshake()
    dev.punch("77", datetime.now().replace(microsecond=0))
    body = client.get("/api/events", params={"after": start, "once": True}).text
    assert "event: punch" in body and '"sn": "EVT1"' in body


def test_server_address_and_ports_are_set_in_the_program(client, tmp_path, monkeypatch):
    from hader import runtime
    from hader.config import read_network, settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    net = client.get("/api/system/network").json()
    assert "0.0.0.0" in net["addresses"] and net["web_port"] == settings.web_port
    assert client.put("/api/system/network", json={"host": "8.8.8.8", "web_port": 8090}).status_code == 422
    assert client.put("/api/system/network", json={"host": "0.0.0.0", "web_port": 8090, "portal_port": 8090}).status_code == 422
    r = client.put("/api/system/network", json={"host": "0.0.0.0", "web_port": 18090, "portal_port": 18091,
                                                 "adms_ports": "18081, 18082"})
    assert r.status_code == 200 and r.json()["restarting"]
    assert read_network(tmp_path) == {"host": "0.0.0.0", "web_port": "18090", "portal_port": "18091",
                                      "adms_ports": "18081,18082"}
    import time
    time.sleep(1.2)
    assert runtime.RESTART.is_set()
    runtime.RESTART.clear()
    assert client.get("/api/ping").json()["ok"]


def test_admin_interface_can_be_limited_to_chosen_devices(client, monkeypatch):
    from hader import portal_gate as G
    assert G.admin_allowed("10.0.0.7", "10.0.0.5-10.0.0.9") and G.admin_allowed("10.0.1.33", "10.0.1.0/24, 10.0.0.2")
    assert not G.admin_allowed("10.0.2.1", "10.0.1.0/24") and not G.admin_allowed("10.0.2.1", "local")
    assert G.admin_allowed("127.0.0.1", "local")
    # saving a rule that would shut out the device in use is refused
    assert client.put("/api/settings", json={"security.admin_from": "10.9.9.9"}).status_code == 422
    # the rule applies to everything but the terminals and the employee portal
    monkeypatch.setattr(G, "admin_allowed", lambda ip, rule: False)
    from hader import store
    from hader.db import session_scope
    with session_scope() as db:
        store.set_(db, "security.admin_from", "10.9.9.9")
    G._ADMIN["at"] = 0.0
    assert client.get("/api/employees").status_code == 403
    assert "غير متاحة" in client.get("/").text
    assert client.get("/me").status_code == 200 and client.get("/api/ping").status_code == 200
    assert client.get("/iclock/cdata", params={"SN": "X1", "options": "all"}).status_code == 200
    with session_scope() as db:
        store.set_(db, "security.admin_from", "")
    G._ADMIN["at"] = 0.0


def test_service_manager_control_is_local_and_needs_the_token(client, monkeypatch):
    from hader import runtime
    from hader.api import system as S
    # only from this PC
    assert client.post("/api/system/control", json={"action": "stop"},
                       headers={"X-Hader-Control": runtime.control_token()}).status_code == 403
    # from this PC, a wrong token is refused and the right one works
    from starlette.requests import Request
    monkeypatch.setattr(Request, "client", property(lambda self: type("C", (), {"host": "127.0.0.1"})()))
    assert client.post("/api/system/control", json={"action": "stop"}, headers={"X-Hader-Control": "x"}).status_code == 403
    called = []
    monkeypatch.setattr(runtime, "request_stop", lambda *a: called.append("stop"))
    r = client.post("/api/system/control", json={"action": "stop"}, headers={"X-Hader-Control": runtime.control_token()})
    assert r.status_code == 200 and called == ["stop"]
