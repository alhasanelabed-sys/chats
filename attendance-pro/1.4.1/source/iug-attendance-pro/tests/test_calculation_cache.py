from datetime import date, datetime, timedelta

from hader import calculation_cache as C, models as m, store
from hader.db import session_scope
from hader.engine import Engine


def seed(client):
    ids = [client.post('/api/employees', json={'emp_code':str(i),'first_name':f'Cache {i}','hire_date':'2020-01-01'}).json()['id'] for i in (701,702)]
    shift = client.get('/api/shifts').json()['rows'][0]['id']
    r=client.post('/api/schedules',json={'shift_id':shift,'employee_ids':ids,'start_date':'2026-01-01','end_date':'2026-12-31'})
    assert r.status_code==200
    C.clear()
    return ids


def calculate(ids):
    with session_scope() as db:
        return Engine(db,date(2026,8,2),date(2026,8,4),employee_ids=ids).run()


def test_late_punch_recalculates_affected_employee_without_stale_results(client,monkeypatch):
    ids=seed(client); calls=[]; real=Engine.day
    def counted(self,emp,day,today):
        calls.append((emp.id,day)); return real(self,emp,day,today)
    monkeypatch.setattr(Engine,'day',counted)
    first=calculate(ids); assert len(first)==6 and len(calls)==6
    calls.clear(); again=calculate(ids)
    assert [r.as_dict() for r in again]==[r.as_dict() for r in first] and not calls
    with session_scope() as db:
        for hour in (8,16):
            db.add(m.Transaction(employee_id=ids[0],emp_code='701',device_sn='',punch_time=datetime(2026,8,2,hour)))
    calls.clear(); changed=calculate(ids)
    assert len(calls)==3 and {emp for emp,_ in calls}=={ids[0]}
    assert changed[0].status=='present' and changed[0].worked==480
    assert next(r for r in changed if r.employee_id==ids[1]).status=='absent'


def test_rules_approval_names_and_schedule_inputs_invalidate_correctly(client):
    ids=seed(client); calculate(ids)
    with session_scope() as db:
        db.get(m.Employee,ids[0]).first_name='New cached name'
    assert calculate(ids)[0].name.startswith('New cached name')
    lt=client.get('/api/leave-types').json()['rows'][0]['id']
    response=client.post('/api/leaves',json={'employee_id':ids[0],'leave_type_id':lt,'start_time':'2026-08-02 08:00','end_time':'2026-08-02 16:00','status':'pending'})
    assert response.status_code==200
    assert calculate(ids)[0].status=='absent'
    with session_scope() as db:
        db.get(m.Leave,response.json()['id']).status='approved'
    assert calculate(ids)[0].status=='leave'
    with session_scope() as db:
        tt=db.get(m.TimeTable,1);tt.check_out=datetime(2026,1,1,15).time()
    assert calculate(ids)[3].required==420


def test_cache_never_reuses_current_or_previous_day_and_returns_independent_values(client,monkeypatch):
    ids=seed(client); rows=calculate(ids); rows[0].exceptions.append('caller mutation')
    assert 'caller mutation' not in calculate(ids)[0].exceptions
    import hader.engine as E
    monkeypatch.setattr(E,'now',lambda:datetime(2026,8,3,7))
    C.clear();calculate(ids); hits=C.stats()['hits'];calculate(ids)
    assert C.stats()['hits']==hits and C.stats()['entries']==0


def test_cache_memory_bound_is_enforced(monkeypatch):
    C.clear();monkeypatch.setattr(C,'MAX_BYTES',1024)
    for number in range(40): C.put(bytes([number])*32, [{'name':str(number)*80}])
    assert C.stats()['compressed_bytes_with_key_allowance']<=1024
    C.clear()
