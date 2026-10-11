import json
from pathlib import Path

from hader import models as m, operability, timekeeping
from hader.db import engine, reload_durability, session_scope
from hader.adms.server import server_timezone


def test_saved_durability_applies_to_same_engine_after_real_restart_step(client):
    from hader.config import settings
    path=settings.data_dir/'storage.ini'; original=path.read_bytes() if path.exists() else None
    try:
        assert client.put('/api/settings',json={'storage.synchronous':'FULL'}).status_code==200
        configured=client.get('/api/operations/health').json()['storage']
        assert configured['configured_synchronous']=='FULL'
        reload_durability()
        active=client.get('/api/operations/health').json()['storage']
        assert active['active_synchronous']=='FULL' and not active['restart_required']
    finally:
        if original is None:path.unlink(missing_ok=True)
        else:path.write_bytes(original)
        reload_durability()


def test_handshake_uses_institutional_iana_time_with_device_override(client):
    from hader.config import settings
    path=settings.data_dir/'timezone.ini';original=path.read_bytes() if path.exists() else None
    try:
        assert client.put('/api/settings',json={'time.zone':'Asia/Kathmandu'}).status_code==200
        with session_scope() as db:
            assert server_timezone(db,None)==6  # ADMS supports integer offsets; calculation keeps full IANA zone.
            device=m.Device(sn='ZONE',alias='ZONE',time_zone=2)
            assert server_timezone(db,device)==2
    finally:
        if original is None:path.unlink(missing_ok=True)
        else:path.write_bytes(original)
        timekeeping._CACHE.clear()


def test_settings_current_policy_projection_and_secret_redaction(client):
    response=client.post('/api/attendance/policies',json={'scope':'rules','effective_from':'2026-01-01','reason':'Test policy projection','changes':{'att.no_out_minutes':91}})
    assert response.status_code==200,response.text
    r=client.put('/api/settings',json={'alerts.smtp_password':'private-test-value'})
    assert r.status_code==200 and r.json()['alerts.smtp_password']=='********'
    assert client.get('/api/settings').json()['att.no_out_minutes']==91
    audits=client.get('/api/audit').json()['rows']
    assert all('private-test-value' not in a['detail'] for a in audits)
