import io
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from hader import models as m
from hader.db import session_scope
from hader.printing import write_document


def test_print_includes_every_row_beyond_screen_page_and_filters(client):
    employee = client.post('/api/employees', json={'emp_code': 'PRINT', 'first_name': '<script>alert(1)</script>'}).json()
    with session_scope() as db:
        for i in range(225):
            db.add(m.Transaction(employee_id=employee['id'], emp_code='PRINT', device_sn='',
                punch_time=datetime(2026, 8, 2, 8) + timedelta(seconds=i), source='import'))
        db.add(m.Transaction(employee_id=employee['id'], emp_code='PRINT', device_sn='', punch_time=datetime(2026, 8, 3, 8)))
    screen = client.get('/api/reports/transactions?start=2026-08-02&end=2026-08-02').json()
    assert screen['total'] == 225 and len(screen['rows']) == 200
    printed = client.get('/api/reports/transactions/print?start=2026-08-02&end=2026-08-02&orientation=portrait')
    assert printed.status_code == 200 and printed.headers['x-print-rows'] == '225'
    assert 'data-row="225"' in printed.text and 'data-row="226"' not in printed.text
    assert 'A4 portrait' in printed.text and '<script>alert(1)</script>' not in printed.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in printed.text
    assert printed.headers['cache-control'] == 'no-store'


def test_wide_month_print_preserves_every_column_and_identity():
    columns = [{'key': k, 'label': k} for k in ['emp_code', 'name', 'department'] + [f'day_{i}' for i in range(1, 32)]]
    out = io.StringIO()
    metadata = write_document({'title': 'Month', 'columns': columns, 'rows': [{col['key']: col['key'] for col in columns}]}, out)
    assert metadata == {'rows': 1, 'sections': 4, 'orientation': 'landscape', 'column_bands': 4}
    for column in columns:
        assert f'<th data-key="{column["key"]}">' in out.getvalue()
    assert out.getvalue().count('<th data-key="emp_code">') == 4
    assert out.getvalue().count('<th data-key="name">') == 4
    assert 'overflow:hidden' not in out.getvalue() and 'table-layout:fixed' in out.getvalue()


def test_print_direction_validation_and_permissions(client):
    assert client.get('/api/reports/daily/print?orientation=diagonal').status_code == 422
    assert client.get('/api/reports/missing/print').status_code == 404
    client.post('/api/auth/logout')
    assert client.get('/api/reports/daily/print').status_code == 401
