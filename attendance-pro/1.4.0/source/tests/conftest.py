import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="hader_test_")
os.environ["HADER_DATA_DIR"] = _tmp
os.environ["HADER_CONFIG"] = str(Path(_tmp) / "none.ini")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from fastapi.testclient import TestClient  # noqa: E402

from hader.app import app  # noqa: E402
from hader.bootstrap import init_db  # noqa: E402
from hader.db import Base, engine  # noqa: E402
from hader.adms.server import TRAFFIC  # noqa: E402

ADMIN_PASSWORD = "admin-test-password"


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    TRAFFIC.clear()
    from hader.api import system as _system
    _system._SNAP.clear()
    from hader import console as _console
    _console._UNANSWERED.clear()
    from hader.adms import sync as _sync
    _sync._ASKED.clear()
    from hader.api import portal as _portal
    _portal._FAILS.clear()
    _portal._IP_FAILS.clear()
    from hader import portal_gate as _gate, runtime as _runtime
    _gate._ADMIN["at"] = 0.0
    _runtime.RESTART.clear()
    _runtime.STOP.clear()
    init_db()
    from hader import models as m, store
    from hader.db import session_scope
    from hader.security import hash_password
    from sqlalchemy import select
    with session_scope() as db:
        admin = db.scalar(select(m.User).where(m.User.username == "admin"))
        admin.password_hash = hash_password(ADMIN_PASSWORD)
        admin.must_change_password = False
        # Normal workflow tests explicitly authorize device writes. Read-only
        # regressions override this setting and must prove the default is safe.
        store.set_(db, "tcp.write_back", True)
    throttle = getattr(_system, "_LOGIN_THROTTLE", None)
    if throttle is not None:
        throttle.clear()
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        yield c


@pytest.fixture()
def device_factory(client):
    from device_simulator import SimDevice

    def transport(method, path, params, body=b""):
        r = client.request(method, path, params=params, content=body)
        assert r.status_code == 200, r.text
        return r.text

    def make(sn="SIM0001", **kw):
        return SimDevice(transport, sn=sn, **kw)
    return make
