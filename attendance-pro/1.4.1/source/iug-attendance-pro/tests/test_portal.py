"""Employee portal and alerts, end to end."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def employee(client):
    dept = client.get("/api/departments").json()["rows"][0]["id"]
    e = client.post("/api/employees", json={"emp_code": "4022", "first_name": "عبد الرحمن",
                                            "last_name": "المقادمة", "department_id": dept}).json()
    r = client.post("/api/employees/portal", json={"action": "enable", "ids": [e["id"]]}).json()
    return e, r["rows"][0]["password"]


def portal(app_client):
    from hader.app import app
    c = TestClient(app)
    c.__enter__()
    return c


def test_sign_in_today_month_and_password(client, employee):
    e, pw = employee
    p = portal(client)
    assert p.get("/api/me").status_code == 401
    assert p.post("/api/me/login", json={"code": "4022", "password": "000000"}).status_code == 401
    r = p.post("/api/me/login", json={"code": "4022", "password": pw}).json()
    assert r["must_change"] is True
    me = p.get("/api/me").json()
    assert me["name"] == "عبد الرحمن المقادمة" and me["today"] is not None
    assert p.get("/api/me", headers={"X-Lang": "en"}).json()["display_name"] == "Abdulrahman Al-Maqadma"
    assert p.get("/api/me/month").status_code == 403
    # an employee session never opens the admin side
    assert p.get("/api/employees").status_code == 401
    assert p.post("/api/me/password", json={"old": pw, "new": "4022"}).status_code == 422
    assert p.post("/api/me/password", json={"old": pw, "new": "n3w-long-pass"}).json()["ok"]
    assert p.get("/api/me").json()["must_change"] is False
    month = p.get("/api/me/month").json()
    assert month["days"] and "present_days" in month["totals"]


def test_requests_flow_with_alerts(client, employee):
    e, pw = employee
    client.put("/api/settings", json={"alerts.enabled": True})
    p = portal(client)
    p.post("/api/me/login", json={"code": "4022", "password": pw})
    assert p.post("/api/me/password", json={"old": pw, "new": "portal-test-password"}).status_code == 200
    lt = p.get("/api/me/leave-types").json()["rows"][0]["id"]
    day = (datetime.now() + timedelta(days=3)).date().isoformat()
    r = p.post("/api/me/requests", json={"kind": "leave", "leave_type_id": lt, "start": day, "end": day,
                                         "reason": "ظرف عائلي"})
    assert r.status_code == 200
    assert p.post("/api/me/requests", json={"kind": "leave", "leave_type_id": lt, "start": day, "end": day}).status_code == 409
    t = (datetime.now() - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    assert p.post("/api/me/requests", json={"kind": "manual", "time": t.isoformat(), "state": 1}).status_code == 200
    reqs = p.get("/api/me/requests").json()["rows"]
    assert {x["kind"] for x in reqs} == {"leave", "manual"} and all(x["status"] == "pending" for x in reqs)
    # the manager is told, in the bell
    bell = client.get("/api/notifications").json()
    assert bell["unread"] >= 2 and any(n["kind"] == "request_new" for n in bell["rows"])
    # the manager approves the leave: the employee is told in the portal
    leave_id = next(x["id"] for x in reqs if x["kind"] == "leave")
    client.post("/api/approvals/leaves", json={"ids": [leave_id], "status": "approved"})
    notes = p.get("/api/me/notifications").json()["rows"]
    assert any(n["kind"] == "request_decided" and "✓" in n["body"] for n in notes)
    bal = {b["id"]: b for b in p.get("/api/me/balances").json()["rows"]}
    assert bal[lt]["used"] == 1.0
    # an answered request cannot be cancelled; a pending one can
    assert p.delete(f"/api/me/requests/leave/{leave_id}").status_code == 409
    manual_id = next(x["id"] for x in reqs if x["kind"] == "manual")
    assert p.delete(f"/api/me/requests/manual/{manual_id}").json()["ok"]


def test_lockout_after_wrong_passwords(client, employee):
    p = portal(client)
    for _ in range(5):
        p.post("/api/me/login", json={"code": "4022", "password": "x"})
    assert p.post("/api/me/login", json={"code": "4022", "password": employee[1]}).status_code == 429
    from hader.api import portal as P
    P._FAILS.clear()


def test_late_and_device_alerts_are_sent_once(client, employee, device_factory):
    from hader import alerts
    from hader.db import session_scope
    e, pw = employee
    client.put("/api/settings", json={"alerts.enabled": True})
    dev = device_factory("ALRT")
    dev.handshake()
    now = datetime.now()
    dev.punch("4022", now.replace(hour=9, minute=40, second=0, microsecond=0) if now.hour >= 10 else now.replace(second=0, microsecond=0))
    alerts.tick({})
    alerts.tick({})
    with session_scope() as db:
        from hader import models as m
        late = db.query(m.Notification).filter_by(kind="late", to_kind="employee").count()
    # a late alert exists only when a timetable applies today; never more than one
    assert late <= 1
    # terminal silent for long -> one offline alert, then one "back online"
    with session_scope() as db:
        from hader import models as m
        d = db.query(m.Device).filter_by(sn="ALRT").one()
        d.last_activity = datetime.now() - timedelta(minutes=30)
    alerts.tick({})
    alerts.tick({})
    bell = client.get("/api/notifications").json()["rows"]
    assert sum(1 for n in bell if n["kind"] == "device_offline") == 1
    dev.handshake()
    alerts.tick({})
    alerts.tick({})
    bell = client.get("/api/notifications").json()["rows"]
    assert sum(1 for n in bell if n["kind"] == "device_online") == 1


def test_alerts_config_and_test_message(client):
    cfg = client.get("/api/alerts/config").json()
    assert cfg["rules"]["absent"]["at"] == "10:00"
    cfg["rules"]["absent"]["at"] = "09:15"
    cfg["settings"]["alerts.smtp_password"] = "secret"
    out = client.put("/api/alerts/config", json=cfg).json()
    assert out["rules"]["absent"]["at"] == "09:15" and out["settings"]["alerts.smtp_password"] == "********"
    r = client.post("/api/alerts/test", json={"channel": "email", "target": "a@b.c"}).json()
    assert r["ok"] is False and "SMTP" in r["error"]


def test_portal_port_serves_the_portal_and_nothing_else(client, employee, monkeypatch):
    from hader.config import settings
    monkeypatch.setattr(settings, "portal_port", 80)   # the test client talks to port 80
    p = portal(client)
    assert p.get("/", follow_redirects=False).headers["location"] == "/me"
    page = p.get("/me")
    assert page.status_code == 200 and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert p.get("/static/me/me.js").status_code == 200
    assert p.post("/api/me/login", json={"code": "4022", "password": employee[1]}).status_code == 200
    assert p.get("/api/me").status_code == 200
    # admin pages, admin API, terminal endpoints and the admin UI files are not there at all
    for path in ("/api/employees", "/api/settings", "/api/auth/login", "/api/docs", "/static/js/pages.js",
                 "/iclock/cdata?SN=X", "/api/events", "/api/portal/info", "/api/employees/1/photo"):
        assert p.get(path).status_code == 404, path
    assert p.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 404
    assert "Disallow: /" in p.get("/robots.txt").text
    # the regular port is unchanged
    monkeypatch.setattr(settings, "portal_port", 0)
    assert client.get("/api/settings").status_code == 200


def test_sign_in_behind_a_tunnel_is_https_and_throttled_per_address(client, employee, monkeypatch):
    from hader import portal_gate as G
    from hader.api import portal as P
    monkeypatch.setattr(G, "TRUSTED_PROXIES", G.TRUSTED_PROXIES | {"testclient"})
    p = portal(client)
    head = {"X-Forwarded-Proto": "https", "CF-Connecting-IP": "203.0.113.7"}
    r = p.post("/api/me/login", json={"code": "4022", "password": employee[1]}, headers=head)
    assert "secure" in r.headers["set-cookie"].lower()
    assert r.headers["strict-transport-security"]
    # one address guessing many numbers is stopped, other addresses are not affected
    for i in range(P.MAX_IP_FAILS):
        p.post("/api/me/login", json={"code": f"9{i:03d}", "password": "123456"}, headers=head)
    assert p.post("/api/me/login", json={"code": "4022", "password": employee[1]}, headers=head).status_code == 429
    other = {"X-Forwarded-Proto": "https", "CF-Connecting-IP": "198.51.100.9"}
    assert p.post("/api/me/login", json={"code": "4022", "password": employee[1]}, headers=other).status_code == 200
    P._FAILS.clear()
    P._IP_FAILS.clear()


def test_portal_info_link_and_qr(client):
    client.put("/api/settings", json={"portal.public_url": "https://portal.example.com/"})
    info = client.get("/api/portal/info").json()
    assert info["public"] == "https://portal.example.com" and info["best"] == "https://portal.example.com"
    assert {"enabled", "active", "signed_in", "pending"} <= set(info)
    svg = client.get("/api/portal/qr.svg", params={"text": info["best"]})
    assert svg.status_code == 200 and svg.text.startswith("<svg") and "http://www.w3.org/2000/svg" in svg.text
