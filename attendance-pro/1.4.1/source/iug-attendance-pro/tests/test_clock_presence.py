from datetime import timedelta
import pytest
from hader import models as m, operability as O, store
from hader.db import now, session_scope

@pytest.mark.parametrize('successful', [True, False])
def test_clock_read_contact_is_independent_of_attendance_upload(client, monkeypatch, successful):
    old = now() - timedelta(hours=1)
    with session_scope() as db:
        store.set_(db, 'tcp.write_back', False)
        d = m.Device(sn='CLOCK-CONTACT', ip='192.0.2.1', last_activity=old, last_sync=old)
        db.add(d)
        db.flush()
        device_id = d.id
    def read(*args):
        if not successful:
            raise OSError('unreachable')
        return now()
    monkeypatch.setattr(O, 'read_clock', read)
    response = client.post(f'/api/operations/devices/{device_id}/clock-check')
    assert response.status_code == 200
    assert response.json()['measured'] is successful
    row = client.get(f'/api/devices/{device_id}').json()
    assert row['state'] == ('online' if successful else 'offline')
    assert row['read_only'] is True
    with session_scope() as db:
        d = db.get(m.Device, device_id)
        assert d.last_sync == old
        assert (d.last_activity > old) is successful
