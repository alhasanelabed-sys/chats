from datetime import datetime
from hader.adms.protocol import AttRecord
from hader.adms import sync
from hader.db import session_scope
from hader.api import system
from hader import models as m


def test_new_today_upload_preserves_old_report_but_late_upload_invalidates(client,monkeypatch):
    employee=client.post('/api/employees',json={'emp_code':'RANGE','first_name':'Range'}).json()
    calls=[];real=system.R.build
    def count(*args,**kw):calls.append(args[1]);return real(*args,**kw)
    monkeypatch.setattr(system.R,'build',count);system._REPORTS.clear()
    url='/api/reports/transactions?start=2026-08-02&end=2026-08-02'
    assert client.get(url).json()['total']==0 and len(calls)==1
    with session_scope() as db:
        sync.save_punches(db,None,[AttRecord('RANGE',datetime(2026,10,10,8))],source='import')
    assert client.get(url).json()['total']==0 and len(calls)==1
    with session_scope() as db:
        sync.save_punches(db,None,[AttRecord('RANGE',datetime(2026,8,2,8))],source='import')
    assert client.get(url).json()['total']==1 and len(calls)==2
    client.put('/api/employees/'+str(employee['id']),json={'first_name':'Renamed'})
    assert client.get(url).json()['rows'][0]['name']=='Renamed' and len(calls)==3


def test_adjacent_night_shift_day_is_included_in_invalidation(client,monkeypatch):
    client.post('/api/employees',json={'emp_code':'NIGHTRANGE','first_name':'Night','hire_date':'2020-01-01'})
    calls=[];real=system.R.build
    def count(*args,**kw):calls.append(args[1]);return real(*args,**kw)
    monkeypatch.setattr(system.R,'build',count);system._REPORTS.clear()
    url='/api/reports/daily?start=2026-08-02&end=2026-08-02'
    assert client.get(url).status_code==200 and len(calls)==1
    with session_scope() as db:
        sync.save_punches(db,None,[AttRecord('NIGHTRANGE',datetime(2026,8,3,4))],source='import')
    assert client.get(url).status_code==200 and len(calls)==2


def test_today_report_expires_with_clock_even_without_new_punch(client,monkeypatch):
    from types import SimpleNamespace
    from hader.db import now
    clock=[0.0];calls=[];real=system.R.build
    monkeypatch.setattr(system,'_time',SimpleNamespace(monotonic=lambda:clock[0]))
    def count(*args,**kw):calls.append(args[1]);return real(*args,**kw)
    monkeypatch.setattr(system.R,'build',count);system._REPORTS.clear()
    url='/api/reports/daily?start='+str(now().date())+'&end='+str(now().date())
    assert client.get(url).status_code==200 and len(calls)==1
    clock[0]=19;assert client.get(url).status_code==200 and len(calls)==1
    clock[0]=21;assert client.get(url).status_code==200 and len(calls)==2
