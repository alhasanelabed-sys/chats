"""The live view is a pageable view of today's recorded movements."""
from datetime import datetime, timedelta

from sqlalchemy import select

from hader import models as m
from hader.db import session_scope


def seed_movements(stamp, count=85):
    with session_scope() as db:
        employee = m.Employee(emp_code='MON-1', first_name='Monitor', last_name='Today')
        db.add(employee)
        db.flush()
        db.add_all([m.Transaction(employee_id=employee.id, emp_code=employee.emp_code,
                                 device_sn='MON-DEVICE', punch_time=stamp + timedelta(seconds=i))
                    for i in range(count)])
        db.add(m.Transaction(employee_id=employee.id, emp_code=employee.emp_code,
                             device_sn='MON-DEVICE', punch_time=stamp - timedelta(days=1)))
        db.add(m.Transaction(employee_id=employee.id, emp_code=employee.emp_code,
                             device_sn='MON-DEVICE', punch_time=stamp + timedelta(days=1)))


def test_monitor_pages_all_today_and_matches_transaction_register(client, monkeypatch):
    from hader.api import devices
    today = datetime(2026, 10, 10, 12)
    monkeypatch.setattr(devices, 'now', lambda: today)
    seed_movements(today.replace(hour=8), 85)
    ids = []
    for offset in (0, 30, 60):
        response = client.get('/api/monitor', params={'offset': offset, 'limit': 30})
        assert response.status_code == 200
        result = response.json()
        assert result['date'] == '2026-10-10'
        assert result['total'] == 85
        assert all(row['punch_time'].startswith('2026-10-10') for row in result['rows'])
        ids.extend(row['id'] for row in result['rows'])
    register = client.get('/api/transactions', params={'start': '2026-10-10', 'end': '2026-10-10', 'limit': 200}).json()
    assert ids == [row['id'] for row in register['rows']]
    assert len(ids) == len(set(ids)) == 85


def test_monitor_incremental_ignores_late_upload_for_previous_day(client, monkeypatch):
    from hader.api import devices
    now = datetime(2026, 10, 10, 12)
    monkeypatch.setattr(devices, 'now', lambda: now)
    seed_movements(now.replace(hour=8), 2)
    cursor = client.get('/api/monitor').json()['last_id']
    with session_scope() as db:
        employee = db.scalar(select(m.Employee).where(m.Employee.emp_code == 'MON-1'))
        db.add_all([m.Transaction(employee_id=employee.id, emp_code=employee.emp_code, device_sn='MON-DEVICE',
                                  punch_time=now - timedelta(days=2)),
                    m.Transaction(employee_id=employee.id, emp_code=employee.emp_code, device_sn='MON-DEVICE',
                                  punch_time=now)])
    result = client.get('/api/monitor', params={'after_id': cursor}).json()
    assert len(result['rows']) == 1
    assert result['rows'][0]['punch_time'] == '2026-10-10 12:00:00'
    assert result['total'] == 3
    monkeypatch.setattr(devices, 'now', lambda: now + timedelta(days=1))
    tomorrow = client.get('/api/monitor').json()
    assert tomorrow['date'] == '2026-10-11' and tomorrow['total'] == 1


def test_monitor_filters_and_bounds(client, monkeypatch):
    from hader.api import devices
    now = datetime(2026, 10, 10, 12)
    monkeypatch.setattr(devices, 'now', lambda: now)
    seed_movements(now.replace(hour=8), 2)
    assert client.get('/api/monitor', params={'q': 'not-an-employee'}).json()['total'] == 0
    assert client.get('/api/monitor', params={'q': 'Monitor', 'device': 'MON-DEVICE'}).json()['total'] == 2
    assert client.get('/api/monitor', params={'device': 'elsewhere'}).json()['total'] == 0
    result = client.get('/api/monitor', params={'offset': -5, 'limit': 999999}).json()
    assert result['offset'] == 0 and result['limit'] <= 1000
