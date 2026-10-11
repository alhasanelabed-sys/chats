"""Virtual connectivity is explicit and does not turn stale hardware online."""
import json
from datetime import timedelta

from hader import models as m, store
from hader.db import now, session_scope


def _device(enabled=True):
    with session_scope() as db:
        stamp = now() - timedelta(hours=1)
        d = m.Device(sn='VIRTUAL-STATUS', alias='Training terminal', enabled=enabled,
                     ip='', tcp_poll=False, last_activity=stamp, last_sync=stamp,
                     options=json.dumps({'synthetic': True, 'physical_device_access': False}))
        db.add(d)
        db.flush()
        return d.id


def test_running_demo_remains_virtually_connected_without_new_punches(client, monkeypatch):
    device_id = _device()
    monkeypatch.setenv('HADER_DEMO_MODE', '1')
    response = client.get(f'/api/devices/{device_id}')
    assert response.status_code == 200
    row = response.json()
    assert row['state'] == 'online'
    assert row['virtual_device'] is True
    assert row['connection_mode'] == 'virtual'
    assert row['status_detail']['code'] == 'virtual_online'
    assert row['read_only'] is True
    assert row['receiving'] is False
    # A running simulator is available without rewriting its historical contact
    # or attendance timestamps to claim a physical heartbeat/upload.
    with session_scope() as db:
        d = db.get(m.Device, device_id)
        assert (now() - d.last_activity).total_seconds() > 3500
        assert (now() - d.last_sync).total_seconds() > 3500


def test_synthetic_metadata_does_not_forge_real_hardware_connectivity(client, monkeypatch):
    device_id = _device()
    monkeypatch.delenv('HADER_DEMO_MODE', raising=False)
    row = client.get(f'/api/devices/{device_id}').json()
    assert row['state'] == 'offline'
    assert row['virtual_device'] is False
    assert row['connection_mode'] != 'virtual'


def test_disabled_virtual_device_is_disabled_and_cannot_transfer(client, monkeypatch):
    device_id = _device(enabled=False)
    monkeypatch.setenv('HADER_DEMO_MODE', '1')
    row = client.get(f'/api/devices/{device_id}').json()
    assert row['state'] == 'disabled'
    assert row['transferring'] is False


def test_demo_is_read_only_even_if_database_write_permission_is_changed(client, monkeypatch):
    device_id = _device()
    monkeypatch.setenv('HADER_DEMO_MODE', '1')
    with session_scope() as db:
        store.set_(db, 'tcp.write_back', True)
    row = client.get(f'/api/devices/{device_id}').json()
    assert row['read_only'] is True
    assert row['virtual_device'] is True
    assert row['transferring'] is False


def test_dashboard_and_health_use_the_same_virtual_connection_contract(client, monkeypatch):
    _device()
    monkeypatch.setenv('HADER_DEMO_MODE', '1')
    from hader.api.system import compute_dashboard
    from hader.operability import health
    with session_scope() as db:
        dashboard = compute_dashboard(db)
        operational = health(db)
    assert dashboard['online'] == 1 and dashboard['offline'] == 0
    device = dashboard['device_list'][0]
    assert device['state'] == 'online' and device['virtual_device'] is True
    assert device['read_only'] is True and device['transferring'] is False
    assert operational['devices'][0]['online'] is True
    assert operational['devices'][0]['virtual_device'] is True
    assert operational['devices'][0]['status_detail'] == device['status_detail']


def test_internal_history_metadata_is_not_a_firmware_option(client):
    device_id = _device()
    with session_scope() as db:
        d = db.get(m.Device, device_id)
        options = json.loads(d.options)
        options['_hader_day_reconciliation'] = {'date': '2026-10-11', 'status': 'requested'}
        d.options = json.dumps(options)
    row = client.get(f'/api/devices/{device_id}').json()
    assert '_hader_day_reconciliation' not in row['options']
    assert row['options']['synthetic'] is True
    with session_scope() as db:
        assert '_hader_day_reconciliation' in json.loads(db.get(m.Device, device_id).options)
